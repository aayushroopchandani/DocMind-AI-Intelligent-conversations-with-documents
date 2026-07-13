from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from functools import lru_cache
from math import inf
from typing import Any, Literal, Sequence

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from qdrant_client import models

from config.settings import settings
from db.models.generated_quiz import (
    GeneratedQuizCreate,
    QuizCitation,
    QuizDifficulty,
    QuizMode,
    QuizQuestionFormat,
)
from qdrant_manager import get_client
from scripts.chat_with_pdf import get_utility_llm
from scripts.intention_pipelines.quiz_pipeline.topic_based import (
    generate_quiz_from_context,
    generate_topic_based_quiz,
)
from utils.format_document import format_documents_with_citations
from utils.pydantic_schemas import Citation

logger = logging.getLogger(__name__)

RECENT_CONTEXT_MESSAGES = 6
MESSAGE_CONTENT_CHARS = 1400
MEMORY_CHARS = 1800
NODE_CHUNKS_PER_NODE = 3
CITATION_CHUNKS_PER_PAGE = 2
SCROLL_PAGE_SIZE = 64

CONTENT_KEY = "page_content"
METADATA_KEY = "metadata"
USER_ID_FIELD = f"{METADATA_KEY}.user_id"
DOC_ID_FIELD = f"{METADATA_KEY}.doc_id"
NODE_ID_FIELD = f"{METADATA_KEY}.node_id"
PAGE_NUMBER_FIELD = f"{METADATA_KEY}.page_number"

ResolutionSource = Literal[
    "recent_messages",
    "memory",
    "node_metadata",
    "citations",
    "unknown",
]


class ContextQuizResolution(BaseModel):
    resolved_topic: str | None = Field(
        default=None,
        description="Concise topic or combined topics the quiz should cover.",
    )
    relevant_message_indexes: list[int] = Field(
        default_factory=list,
        description="Indexes of recent messages that define the referenced context.",
    )
    source: ResolutionSource = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    use_node_metadata: bool = True
    use_citations: bool = True
    needs_semantic_retrieval: bool = False
    reason: str = ""


@lru_cache(maxsize=1)
def _get_context_resolver_llm():
    return get_utility_llm().with_structured_output(ContextQuizResolution)


def _chunk_collection_name() -> str:
    collection_name = os.getenv("QDRANT_COLLECTION_NAME")
    if not collection_name:
        raise RuntimeError("QDRANT_COLLECTION_NAME is not configured")
    return collection_name


def _truncate(value: Any, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _integer_or_infinity(value: Any) -> int | float:
    if value is None:
        return inf
    try:
        return int(value)
    except (TypeError, ValueError):
        return inf


def _allowed_doc_ids(doc_ids: list[str]) -> set[str]:
    return {str(doc_id) for doc_id in doc_ids if doc_id}


def _normalize_node_metadata(
    value: Any,
    allowed_doc_ids: set[str],
) -> list[dict[str, list[str] | str]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, list[str] | str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        doc_id = str(item.get("doc_id") or "")
        if not doc_id or doc_id not in allowed_doc_ids:
            continue

        node_ids = [
            str(node_id)
            for node_id in item.get("node_ids", [])
            if node_id is not None
        ]
        node_ids = list(dict.fromkeys(node_ids))
        if node_ids:
            normalized.append({"doc_id": doc_id, "node_ids": node_ids})

    return normalized


def _normalize_citations(value: Any, allowed_doc_ids: set[str]) -> list[QuizCitation]:
    if not isinstance(value, list):
        return []

    citations: list[QuizCitation] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        doc_id = str(item.get("document_id") or "")
        if not doc_id or doc_id not in allowed_doc_ids:
            continue
        try:
            citations.append(
                QuizCitation(
                    document_id=doc_id,
                    document_name=str(item.get("document_name") or "document.pdf"),
                    page_number=item.get("page_number"),
                    chunk_id=item.get("chunk_id"),
                    excerpt=item.get("excerpt"),
                )
            )
        except Exception:
            logger.debug("Skipping invalid quiz citation metadata", exc_info=True)

    return citations


def _recent_message_entries(
    conversation: list[dict[str, Any]],
    *,
    allowed_doc_ids: set[str],
) -> list[dict[str, Any]]:
    start_index = max(0, len(conversation) - RECENT_CONTEXT_MESSAGES)
    entries: list[dict[str, Any]] = []

    for absolute_index, message in enumerate(conversation[start_index:], start=start_index):
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        node_metadata = _normalize_node_metadata(
            meta.get("node_metadata"),
            allowed_doc_ids,
        )
        citations = _normalize_citations(meta.get("citations"), allowed_doc_ids)
        entries.append(
            {
                "index": absolute_index,
                "role": message.get("role"),
                "content": message.get("content") or "",
                "node_metadata": node_metadata,
                "citations": citations,
            }
        )

    return entries


def _resolver_payload(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for entry in entries:
        payload.append(
            {
                "index": entry["index"],
                "role": entry["role"],
                "content": _truncate(entry["content"], MESSAGE_CONTENT_CHARS),
                "node_metadata": entry["node_metadata"],
                "citations": [
                    citation.model_dump(mode="json")
                    for citation in entry["citations"][:4]
                ],
            }
        )
    return payload


def _system_prompt() -> str:
    return (
        "Resolve context-based quiz references for a PDF chat app. "
        "The current user is asking for a quiz on 'this', 'that', 'above', "
        "or multiple recently discussed topics. Inspect all recent messages, "
        "not only the latest message. Return the message indexes that define "
        "the quiz scope. Prefer indexes whose assistant messages contain "
        "node metadata or citations. If the request refers to multiple topics, "
        "combine them in resolved_topic and include all relevant indexes. "
        "Set needs_semantic_retrieval only when recent metadata/citations are "
        "not enough to identify document scope."
    )


def _human_prompt(
    *,
    question: str,
    memory_summary: str,
    doc_ids: list[str],
    entries: list[dict[str, Any]],
) -> str:
    return (
        "Current user quiz request:\n"
        f"{question}\n\n"
        "Selected document ids:\n"
        f"{json.dumps(doc_ids, ensure_ascii=False)}\n\n"
        "Conversation memory summary, if any:\n"
        f"{_truncate(memory_summary, MEMORY_CHARS) or '(none)'}\n\n"
        "Recent messages JSON:\n"
        f"{json.dumps(_resolver_payload(entries), ensure_ascii=False)}"
    )


async def _resolve_context_reference(
    *,
    question: str,
    memory_summary: str,
    doc_ids: list[str],
    entries: list[dict[str, Any]],
) -> ContextQuizResolution:
    if not entries and not memory_summary:
        return ContextQuizResolution(
            resolved_topic=None,
            source="unknown",
            confidence=0.0,
            needs_semantic_retrieval=True,
            reason="No prior chat context is available.",
        )

    try:
        return await _get_context_resolver_llm().ainvoke(
            [
                SystemMessage(content=_system_prompt()),
                HumanMessage(
                    content=_human_prompt(
                        question=question,
                        memory_summary=memory_summary,
                        doc_ids=doc_ids,
                        entries=entries,
                    )
                ),
            ]
        )
    except Exception:
        logger.exception("Context quiz reference resolution failed")
        return ContextQuizResolution(
            resolved_topic=None,
            relevant_message_indexes=[entry["index"] for entry in entries],
            source="recent_messages",
            confidence=0.25,
            use_node_metadata=True,
            use_citations=True,
            needs_semantic_retrieval=True,
            reason="Resolver failed; using recent chat context as fallback.",
        )


def _selected_entries(
    entries: list[dict[str, Any]],
    resolution: ContextQuizResolution,
) -> list[dict[str, Any]]:
    by_index = {entry["index"]: entry for entry in entries}
    selected = [
        by_index[index]
        for index in resolution.relevant_message_indexes
        if index in by_index
    ]
    return selected or entries


def _merge_node_metadata(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_doc: dict[str, list[str]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)

    for entry in entries:
        for item in entry["node_metadata"]:
            doc_id = str(item["doc_id"])
            for node_id in item["node_ids"]:
                if node_id in seen[doc_id]:
                    continue
                seen[doc_id].add(node_id)
                by_doc[doc_id].append(str(node_id))

    return [
        {"doc_id": doc_id, "node_ids": node_ids}
        for doc_id, node_ids in by_doc.items()
        if node_ids
    ]


def _merge_citations(entries: list[dict[str, Any]]) -> list[QuizCitation]:
    citations: list[QuizCitation] = []
    seen: set[tuple[str, int | None, str | None]] = set()

    for entry in entries:
        for citation in entry["citations"]:
            key = (citation.document_id, citation.page_number, citation.chunk_id)
            if key in seen:
                continue
            seen.add(key)
            citations.append(citation)

    return citations


def _scroll_limited_chunk_records(
    *,
    scroll_filter: models.Filter,
    limit: int,
) -> list[models.Record]:
    qdrant = get_client()
    collection_name = _chunk_collection_name()
    records: list[models.Record] = []
    offset: Any = None

    while len(records) < limit:
        batch, next_offset = qdrant.scroll(
            collection_name=collection_name,
            scroll_filter=scroll_filter,
            limit=min(SCROLL_PAGE_SIZE, limit - len(records)),
            offset=offset,
            with_payload=[CONTENT_KEY, METADATA_KEY],
            with_vectors=False,
        )
        records.extend(batch)

        if next_offset is None or next_offset == offset:
            break
        offset = next_offset

    return records


def _records_to_documents(
    records: list[models.Record],
    *,
    node_order: dict[str, int] | None = None,
) -> list[Document]:
    documents: list[Document] = []
    unknown_node_position = len(node_order or {})

    def sort_key(record: models.Record) -> tuple:
        payload = record.payload or {}
        metadata = payload.get(METADATA_KEY, {})
        if not isinstance(metadata, dict):
            metadata = {}
        return (
            node_order.get(str(metadata.get("node_id")), unknown_node_position)
            if node_order
            else 0,
            _integer_or_infinity(metadata.get("document_chunk_index")),
            _integer_or_infinity(metadata.get("chunk_index")),
            _integer_or_infinity(metadata.get("page_number")),
            _integer_or_infinity(metadata.get("start_index")),
            str(record.id),
        )

    for record in sorted(records, key=sort_key):
        payload = record.payload or {}
        metadata = payload.get(METADATA_KEY, {})
        if not isinstance(metadata, dict):
            continue
        content = str(payload.get(CONTENT_KEY) or "").strip()
        if not content:
            continue
        documents.append(Document(page_content=content, metadata=metadata))

    return documents


async def _retrieve_documents_by_node_metadata(
    *,
    user_id: str,
    node_metadata: list[dict[str, Any]],
    max_total: int,
) -> list[Document]:
    documents: list[Document] = []

    for item in node_metadata:
        if len(documents) >= max_total:
            break

        doc_id = str(item.get("doc_id") or "")
        node_ids = [
            str(node_id)
            for node_id in item.get("node_ids", [])
            if node_id is not None
        ]
        node_ids = list(dict.fromkeys(node_ids))
        if not doc_id or not node_ids:
            continue

        remaining = max_total - len(documents)
        per_doc_limit = min(
            remaining,
            max(settings.retrieval_max_per_doc, len(node_ids) * NODE_CHUNKS_PER_NODE),
        )
        filter_conditions = [
            models.FieldCondition(
                key=USER_ID_FIELD,
                match=models.MatchValue(value=user_id),
            ),
            models.FieldCondition(
                key=DOC_ID_FIELD,
                match=models.MatchValue(value=doc_id),
            ),
            models.FieldCondition(
                key=NODE_ID_FIELD,
                match=models.MatchAny(any=node_ids),
            ),
        ]
        records = await asyncio.to_thread(
            _scroll_limited_chunk_records,
            scroll_filter=models.Filter(must=filter_conditions),
            limit=per_doc_limit,
        )
        node_order = {node_id: index for index, node_id in enumerate(node_ids)}
        documents.extend(
            _records_to_documents(records, node_order=node_order)[:remaining]
        )

    return documents[:max_total]


async def _retrieve_documents_by_citation_pages(
    *,
    user_id: str,
    citations: list[QuizCitation],
    max_total: int,
) -> list[Document]:
    pages_by_doc: dict[str, list[int]] = defaultdict(list)
    seen_pages: dict[str, set[int]] = defaultdict(set)

    for citation in citations:
        if citation.page_number is None:
            continue
        page = int(citation.page_number)
        if page in seen_pages[citation.document_id]:
            continue
        seen_pages[citation.document_id].add(page)
        pages_by_doc[citation.document_id].append(page)

    documents: list[Document] = []
    for doc_id, pages in pages_by_doc.items():
        if len(documents) >= max_total:
            break

        remaining = max_total - len(documents)
        per_doc_limit = min(
            remaining,
            max(settings.retrieval_max_per_doc, len(pages) * CITATION_CHUNKS_PER_PAGE),
        )
        filter_conditions = [
            models.FieldCondition(
                key=USER_ID_FIELD,
                match=models.MatchValue(value=user_id),
            ),
            models.FieldCondition(
                key=DOC_ID_FIELD,
                match=models.MatchValue(value=doc_id),
            ),
            models.FieldCondition(
                key=PAGE_NUMBER_FIELD,
                match=models.MatchAny(any=pages),
            ),
        ]
        records = await asyncio.to_thread(
            _scroll_limited_chunk_records,
            scroll_filter=models.Filter(must=filter_conditions),
            limit=per_doc_limit,
        )
        documents.extend(_records_to_documents(records)[:remaining])

    return documents[:max_total]


def _to_quiz_citation(citation: Citation) -> QuizCitation:
    return QuizCitation(
        document_id=citation.document_id,
        document_name=citation.document_name,
        page_number=citation.page_number,
        chunk_id=citation.chunk_id,
        excerpt=citation.excerpt,
    )


def _resolved_target(
    *,
    resolution: ContextQuizResolution,
    entries: list[dict[str, Any]],
    memory_summary: str,
) -> str:
    if resolution.resolved_topic and resolution.resolved_topic.strip():
        return resolution.resolved_topic.strip()

    for entry in reversed(entries):
        content = _truncate(entry.get("content"), 180)
        if content:
            return f"recent discussion: {content}"

    if memory_summary.strip():
        return f"chat memory: {_truncate(memory_summary, 180)}"

    return "recent conversation context"


def _semantic_fallback_query(
    *,
    question: str,
    target: str,
    entries: list[dict[str, Any]],
    memory_summary: str,
) -> str:
    snippets = [
        _truncate(entry["content"], 500)
        for entry in entries
        if entry.get("content")
    ]
    parts = [
        f"Current request: {question}",
        f"Resolved context topic: {target}",
    ]
    if snippets:
        parts.append("Recent conversation:\n" + "\n".join(snippets))
    if memory_summary.strip():
        parts.append("Memory summary:\n" + _truncate(memory_summary, MEMORY_CHARS))
    return "\n\n".join(parts)


async def generate_context_based_quiz(
    *,
    user_id: str,
    chat_id: str,
    doc_ids: list[str],
    document_names: dict[str, str] | None = None,
    question: str,
    conversation: list[dict[str, Any]],
    memory_summary: str = "",
    number_of_questions: int | None = None,
    difficulty: QuizDifficulty | str | None = None,
    question_formats: Sequence[QuizQuestionFormat | str] | None = None,
    mode: QuizMode | str | None = None,
) -> GeneratedQuizCreate:
    """
    Generate a quiz for references like "quiz me on this".

    The pipeline resolves the implicit context from recent conversation, tries
    stored node metadata first, then previous citation pages, then semantic
    retrieval through the topic-based pipeline.
    """
    if not doc_ids:
        raise ValueError("A context-based quiz requires at least one document.")

    allowed_doc_ids = _allowed_doc_ids(doc_ids)
    entries = _recent_message_entries(
        conversation,
        allowed_doc_ids=allowed_doc_ids,
    )
    if not entries and not memory_summary.strip():
        raise ValueError("I need prior conversation context to generate this quiz.")

    resolution = await _resolve_context_reference(
        question=question,
        memory_summary=memory_summary,
        doc_ids=doc_ids,
        entries=entries,
    )
    selected = _selected_entries(entries, resolution)
    target = _resolved_target(
        resolution=resolution,
        entries=selected,
        memory_summary=memory_summary,
    )

    documents: list[Document] = []
    node_metadata = _merge_node_metadata(selected)
    if not node_metadata:
        node_metadata = _merge_node_metadata(entries)
    if node_metadata:
        documents = await _retrieve_documents_by_node_metadata(
            user_id=user_id,
            node_metadata=node_metadata,
            max_total=settings.retrieval_final_chunks,
        )

    if not documents:
        citations = _merge_citations(selected)
        if not citations:
            citations = _merge_citations(entries)
        if citations:
            documents = await _retrieve_documents_by_citation_pages(
                user_id=user_id,
                citations=citations,
                max_total=settings.retrieval_final_chunks,
            )

    if documents:
        context, citations = format_documents_with_citations(
            documents,
            document_names=document_names or {},
            max_context_tokens=settings.retrieval_max_context_tokens,
        )
        return await generate_quiz_from_context(
            user_id=user_id,
            chat_id=chat_id,
            doc_ids=doc_ids,
            quiz_scope="context_based",
            target=target,
            query=question,
            context=context,
            allowed_citations=[
                _to_quiz_citation(citation) for citation in citations
            ],
            number_of_questions=number_of_questions,
            difficulty=difficulty,
            question_formats=question_formats,
            mode=mode,
        )

    quiz = await generate_topic_based_quiz(
        user_id=user_id,
        chat_id=chat_id,
        doc_ids=doc_ids,
        target=target,
        document_names=document_names,
        query=_semantic_fallback_query(
            question=question,
            target=target,
            entries=selected,
            memory_summary=memory_summary,
        ),
        number_of_questions=number_of_questions,
        difficulty=difficulty,
        question_formats=question_formats,
        mode=mode,
    )
    quiz.quiz_scope = "context_based"
    quiz.target = target
    return quiz
