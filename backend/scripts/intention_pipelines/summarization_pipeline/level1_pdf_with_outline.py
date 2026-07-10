from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, AsyncGenerator

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from db import crud
from utils.pydantic_schemas import (
    AnswerStatus,
    Citation,
    DocMindResponse,
    DocumentContribution,
)

from .utils.operation_on_nodes import get_node_scope
from .utils.searching_for_chunks_with_node_id import searching_for_chunks_with_node_id
from .utils.searching_for_nodes import exact_matching, hybrid_search, normalize_title

load_dotenv()

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


SMALL_MAX_CHUNKS = _env_int("SUMMARY_SMALL_MAX_CHUNKS", 12)
MEDIUM_MAX_CHUNKS = max(
    SMALL_MAX_CHUNKS + 1,
    _env_int("SUMMARY_MEDIUM_MAX_CHUNKS", 60),
)
MAP_GROUP_CHUNKS = _env_int("SUMMARY_MAP_GROUP_CHUNKS", 10)
HIERARCHICAL_GROUP_CHUNKS = _env_int("SUMMARY_HIERARCHICAL_GROUP_CHUNKS", 8)
MAX_PARALLEL_LLM_CALLS = _env_int("SUMMARY_MAX_PARALLEL_LLM_CALLS", 4)
MAX_CHUNK_CHARS = _env_int("SUMMARY_MAX_CHUNK_CHARS", 1400)
MAX_CITATIONS_PER_DOC = _env_int("SUMMARY_MAX_CITATIONS_PER_DOC", 8)
MAX_CONTEXT_CHARS_PER_DOC = _env_int("SUMMARY_MAX_CONTEXT_CHARS_PER_DOC", 22000)

SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "google/gemini-2.5-flash")
SUMMARY_UTILITY_MODEL = os.getenv(
    "SUMMARY_UTILITY_MODEL",
    "google/gemini-2.5-flash-lite",
)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(slots=True)
class DocumentSummaryContext:
    doc_id: str
    doc_name: str
    status: str
    target_label: str = "the whole document"
    node_id: str | None = None
    strategy: str = ""
    context: str = ""
    chunks: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""


@lru_cache(maxsize=1)
def get_summary_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=SUMMARY_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=0.2,
    )


@lru_cache(maxsize=1)
def get_summary_utility_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=SUMMARY_UTILITY_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=0,
    )


@lru_cache(maxsize=1)
def get_streaming_summary_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=SUMMARY_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=0.2,
        streaming=True,
    )


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _integer_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _chunk_page_number(chunk: dict[str, Any]) -> int | None:
    metadata = chunk.get("metadata") or {}
    for key in ("page_number", "page", "page_num", "pageNumber"):
        page = _integer_or_none(metadata.get(key))
        if page is None:
            continue
        if key == "page":
            return page + 1
        return page

    loc = metadata.get("loc")
    if isinstance(loc, dict):
        page = _integer_or_none(loc.get("pageNumber") or loc.get("page_number"))
        if page is not None:
            return page

    return None


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 18:
        return value[:max_chars]
    return value[: max_chars - 18].rstrip() + " ...[truncated]"


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content or "")


def _unique_pages(chunks: list[dict[str, Any]]) -> list[int]:
    pages: set[int] = set()
    for chunk in chunks:
        page_number = _chunk_page_number(chunk)
        if page_number is not None:
            pages.add(page_number)
    return sorted(pages)


def _page_label(chunks: list[dict[str, Any]]) -> str:
    pages = _unique_pages(chunks)
    if not pages:
        return "unknown pages"
    if len(pages) == 1:
        return f"page {pages[0]}"
    return f"pages {pages[0]}-{pages[-1]}"


def _format_chunks(chunks: list[dict[str, Any]]) -> str:
    formatted: list[str] = []
    for position, chunk in enumerate(chunks, start=1):
        metadata = chunk.get("metadata") or {}
        page_number = _chunk_page_number(chunk)
        node_id = metadata.get("node_id")
        chunk_index = metadata.get("chunk_index", position - 1)
        text = _truncate(_clean_text(chunk.get("page_content")), MAX_CHUNK_CHARS)
        if not text:
            continue

        formatted.append(
            "\n".join(
                [
                    (
                        f"[chunk {position} | node={node_id or 'unknown'} | "
                        f"page={page_number or 'unknown'} | chunk_index={chunk_index}]"
                    ),
                    text,
                ]
            )
        )
    return "\n\n".join(formatted)


def _chunk_groups(
    chunks: list[dict[str, Any]],
    group_size: int,
) -> list[list[dict[str, Any]]]:
    return [
        chunks[index : index + group_size]
        for index in range(0, len(chunks), group_size)
    ]


def _nodes_by_id(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(node["node_id"]): node
        for node in nodes
        if node.get("node_id") is not None
    }


def _ensure_normalized_titles(nodes: list[dict[str, Any]]) -> None:
    for node in nodes:
        if not node.get("normalized_title"):
            node["normalized_title"] = normalize_title(str(node.get("title", "")))


def _extract_numbered_target(target: str) -> tuple[str, str] | None:
    match = re.search(
        r"\b(chapter|section|subsection|part|unit|module)\s+(\d+(?:\.\d+)*)\b",
        target,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).lower(), match.group(2)

    match = re.fullmatch(r"\s*(\d+(?:\.\d+)*)\s*", target)
    if match:
        return "numbered", match.group(1)

    return None


def _node_number_prefix(node: dict[str, Any]) -> str | None:
    title = _clean_text(node.get("title"))
    if not title:
        return None

    match = re.match(
        r"^(?:chapter|section|subsection|part|unit|module)?\s*"
        r"(\d+(?:\.\d+)*)\b",
        title,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1)


def _number_depth(value: str) -> int:
    return value.count(".") + 1


def _resolve_numbered_outline_target(
    target: str,
    nodes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    numbered_target = _extract_numbered_target(target)
    if numbered_target is None:
        return None

    label, requested_number = numbered_target
    requested_depth = _number_depth(requested_number)
    candidates: list[dict[str, Any]] = []

    for node in nodes:
        title = _clean_text(node.get("title"))
        if not title:
            continue

        title_lower = title.lower()
        number_prefix = _node_number_prefix(node)
        if number_prefix != requested_number:
            continue

        score = 0
        if label != "numbered" and re.search(rf"\b{re.escape(label)}\b", title_lower):
            score += 100
        elif label == "numbered":
            score += 20

        if _number_depth(number_prefix) == requested_depth:
            score += 15

        level = _integer_or_none(node.get("level"))
        if label == "chapter" and level == 1:
            score += 20
        elif label in {"section", "subsection"} and level is not None and level > 1:
            score += 10

        candidates.append({"node": node, "score": score})

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            item["score"],
            -(_integer_or_none(item["node"].get("page_start")) or 10**9),
        ),
        reverse=True,
    )

    best_score = candidates[0]["score"]
    best_candidates = [
        item["node"]
        for item in candidates
        if item["score"] == best_score
    ]

    if len(best_candidates) == 1:
        node = best_candidates[0]
        return {
            "status": "matched",
            "node_id": node["node_id"],
            "title": node.get("title"),
            "node": node,
        }

    return {
        "status": "ambiguous",
        "node_id": None,
        "candidates": [
            {
                "node_id": node.get("node_id"),
                "title": node.get("title"),
                "parent_id": node.get("parent_id"),
                "page_start": node.get("page_start"),
            }
            for node in best_candidates
        ],
    }


def _node_path(nodes: list[dict[str, Any]], node_id: str | None) -> str:
    if node_id is None:
        return "the whole document"

    by_id = _nodes_by_id(nodes)
    path: list[str] = []
    current_id: str | None = node_id
    seen: set[str] = set()

    while current_id and current_id not in seen:
        seen.add(current_id)
        node = by_id.get(current_id)
        if node is None:
            break
        title = _clean_text(node.get("title"))
        if title:
            path.append(title)
        parent_id = node.get("parent_id")
        current_id = str(parent_id) if parent_id else None

    if not path:
        return node_id
    return " > ".join(reversed(path))


def _candidate_label(
    candidate: dict[str, Any],
    nodes: list[dict[str, Any]],
) -> str:
    node_id = candidate.get("node_id")
    title = _node_path(nodes, str(node_id) if node_id else None)
    page = candidate.get("page_start")
    page_label = f", page {page}" if page is not None else ""
    return f"`{node_id}` - {title}{page_label}"


async def _resolve_node_id(
    *,
    target: str | None,
    nodes: list[dict[str, Any]],
    doc_id: str,
    user_id: str,
) -> tuple[str, str | None, list[dict[str, Any]], str]:
    """
    Return (status, node_id, candidates, message).

    status values:
        matched: node_id may be None for whole-document summaries
        ambiguous: candidates contains exact outline matches
        not_found: no suitable outline node could be resolved
    """
    _ensure_normalized_titles(nodes)

    if not target or not target.strip():
        return "matched", None, [], ""

    cleaned_target = target.strip()
    by_id = _nodes_by_id(nodes)
    if cleaned_target in by_id:
        return "matched", cleaned_target, [], ""

    numbered_match = _resolve_numbered_outline_target(cleaned_target, nodes)
    if numbered_match is not None:
        if numbered_match["status"] == "matched":
            return "matched", str(numbered_match["node_id"]), [], ""
        return "ambiguous", None, numbered_match.get("candidates", []), ""

    match = exact_matching(cleaned_target, nodes)
    if match["status"] == "matched":
        return "matched", str(match["node_id"]), [], ""

    if match["status"] == "ambiguous":
        return "ambiguous", None, match.get("candidates", []), ""

    hybrid_match = await hybrid_search(
        target=cleaned_target,
        nodes=nodes,
        doc_id=doc_id,
        user_id=user_id,
    )
    if hybrid_match.get("status") == "matched" and hybrid_match.get("node_id"):
        return "matched", str(hybrid_match["node_id"]), [], ""

    return (
        "not_found",
        None,
        hybrid_match.get("candidates", []),
        f'I could not find an outline heading matching "{cleaned_target}".',
    )


async def _invoke_summary_llm(
    messages: list[SystemMessage | HumanMessage],
    semaphore: asyncio.Semaphore,
    *,
    utility: bool = True,
) -> str:
    llm = get_summary_utility_llm() if utility else get_summary_llm()
    async with semaphore:
        result = await llm.ainvoke(messages)
    return _message_text(result.content).strip()


async def _summarize_chunk_group(
    *,
    doc_name: str,
    target_label: str,
    group_label: str,
    chunks: list[dict[str, Any]],
    semaphore: asyncio.Semaphore,
) -> str:
    if not chunks:
        return ""

    messages = [
        SystemMessage(
            content=(
                "You summarize PDF chunks for a larger outline-aware "
                "summarization pipeline. Use only the provided chunks. Preserve "
                "important facts, definitions, arguments, examples, formulas, "
                "and named entities. Keep the output compact and factual."
            )
        ),
        HumanMessage(
            content=(
                f"Document: {doc_name}\n"
                f"Scope: {target_label}\n"
                f"Chunk group: {group_label} ({_page_label(chunks)})\n\n"
                "Summarize this group into dense notes that can be safely used "
                "to build a final user-facing summary.\n\n"
                f"{_format_chunks(chunks)}"
            )
        ),
    ]
    return await _invoke_summary_llm(messages, semaphore, utility=True)


async def _merge_summary_parts(
    *,
    doc_name: str,
    target_label: str,
    part_label: str,
    parts: list[str],
    semaphore: asyncio.Semaphore,
) -> str:
    cleaned_parts = [part.strip() for part in parts if part and part.strip()]
    if not cleaned_parts:
        return ""
    if len(cleaned_parts) == 1:
        return cleaned_parts[0]

    numbered_parts = "\n\n".join(
        f"Part {index}:\n{part}"
        for index, part in enumerate(cleaned_parts, start=1)
    )
    messages = [
        SystemMessage(
            content=(
                "You merge partial summaries into one coherent outline-aware "
                "summary. Do not add facts that are not present in the parts."
            )
        ),
        HumanMessage(
            content=(
                f"Document: {doc_name}\n"
                f"Scope: {target_label}\n"
                f"Merge target: {part_label}\n\n"
                "Create one concise but complete summary from these parts. "
                "Preserve the section hierarchy when it matters.\n\n"
                f"{numbered_parts}"
            )
        ),
    ]
    return await _invoke_summary_llm(messages, semaphore, utility=True)


async def _map_reduce_context(
    *,
    doc_name: str,
    target_label: str,
    chunks: list[dict[str, Any]],
    semaphore: asyncio.Semaphore,
) -> str:
    groups = _chunk_groups(chunks, MAP_GROUP_CHUNKS)
    summaries = await asyncio.gather(
        *[
            _summarize_chunk_group(
                doc_name=doc_name,
                target_label=target_label,
                group_label=f"{index + 1}/{len(groups)}",
                chunks=group,
                semaphore=semaphore,
            )
            for index, group in enumerate(groups)
        ]
    )

    return "\n\n".join(
        f"Group {index} summary:\n{summary}"
        for index, summary in enumerate(summaries, start=1)
        if summary
    )


async def _summarize_chunks_to_notes(
    *,
    doc_name: str,
    target_label: str,
    node_label: str,
    chunks: list[dict[str, Any]],
    semaphore: asyncio.Semaphore,
) -> str:
    if not chunks:
        return ""
    if len(chunks) <= HIERARCHICAL_GROUP_CHUNKS:
        return await _summarize_chunk_group(
            doc_name=doc_name,
            target_label=target_label,
            group_label=node_label,
            chunks=chunks,
            semaphore=semaphore,
        )

    groups = _chunk_groups(chunks, HIERARCHICAL_GROUP_CHUNKS)
    group_summaries = await asyncio.gather(
        *[
            _summarize_chunk_group(
                doc_name=doc_name,
                target_label=target_label,
                group_label=f"{node_label}, group {index + 1}/{len(groups)}",
                chunks=group,
                semaphore=semaphore,
            )
            for index, group in enumerate(groups)
        ]
    )
    return await _merge_summary_parts(
        doc_name=doc_name,
        target_label=target_label,
        part_label=node_label,
        parts=group_summaries,
        semaphore=semaphore,
    )


async def _hierarchical_context(
    *,
    doc_name: str,
    target_label: str,
    node_id: str | None,
    nodes: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    semaphore: asyncio.Semaphore,
) -> str:
    scope_ids = get_node_scope(nodes, node_id)
    ordered_ids = (
        scope_ids
        if scope_ids is not None
        else [
            str(node["node_id"])
            for node in nodes
            if node.get("node_id") is not None
        ]
    )
    scope = set(ordered_ids)
    by_id = _nodes_by_id(nodes)

    chunks_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unassigned_chunks: list[dict[str, Any]] = []
    for chunk in chunks:
        metadata = chunk.get("metadata") or {}
        current_node_id = metadata.get("node_id")
        if current_node_id is not None:
            chunks_by_node[str(current_node_id)].append(chunk)
        elif node_id is None:
            unassigned_chunks.append(chunk)

    children_by_parent: dict[str, list[str]] = defaultdict(list)
    root_ids: list[str] = []
    for current_id in ordered_ids:
        node = by_id.get(current_id)
        if node is None:
            continue
        parent_id = node.get("parent_id")
        parent_id = str(parent_id) if parent_id else None
        if parent_id and parent_id in scope:
            children_by_parent[parent_id].append(current_id)
        else:
            root_ids.append(current_id)

    async def summarize_node(current_id: str) -> str:
        node = by_id.get(current_id)
        if node is None:
            return ""

        title = _node_path(nodes, current_id)
        own_chunks = chunks_by_node.get(current_id, [])
        own_summary_task = _summarize_chunks_to_notes(
            doc_name=doc_name,
            target_label=target_label,
            node_label=title,
            chunks=own_chunks,
            semaphore=semaphore,
        )
        child_tasks = [
            summarize_node(child_id)
            for child_id in children_by_parent.get(current_id, [])
        ]

        own_summary, *child_summaries = await asyncio.gather(
            own_summary_task,
            *child_tasks,
        )
        parts = [
            part
            for part in [own_summary, *child_summaries]
            if part and part.strip()
        ]
        return await _merge_summary_parts(
            doc_name=doc_name,
            target_label=target_label,
            part_label=title,
            parts=parts,
            semaphore=semaphore,
        )

    root_tasks = [summarize_node(root_id) for root_id in root_ids]
    if unassigned_chunks:
        root_tasks.append(
            _summarize_chunks_to_notes(
                doc_name=doc_name,
                target_label=target_label,
                node_label="Unassigned document pages",
                chunks=unassigned_chunks,
                semaphore=semaphore,
            )
        )

    root_summaries = await asyncio.gather(*root_tasks)
    return await _merge_summary_parts(
        doc_name=doc_name,
        target_label=target_label,
        part_label=target_label,
        parts=root_summaries,
        semaphore=semaphore,
    )


async def _prepare_document_context(
    *,
    target: str | None,
    doc_id: str,
    user_id: str,
    doc_name: str,
    semaphore: asyncio.Semaphore,
) -> DocumentSummaryContext:
    document = await crud.get_document_nodes(
        user_id=user_id,
        document_id=doc_id,
    )
    if document is None:
        return DocumentSummaryContext(
            doc_id=doc_id,
            doc_name=doc_name,
            status="not_found",
            message="Document metadata was not found.",
        )

    nodes = document.get("nodes", {}).get("nodes", [])
    if not nodes:
        return DocumentSummaryContext(
            doc_id=doc_id,
            doc_name=doc_name,
            status="not_found",
            message="This PDF does not have an outline stored yet.",
        )

    status, node_id, candidates, message = await _resolve_node_id(
        target=target,
        nodes=nodes,
        doc_id=doc_id,
        user_id=user_id,
    )
    if status != "matched":
        return DocumentSummaryContext(
            doc_id=doc_id,
            doc_name=doc_name,
            status=status,
            nodes=nodes,
            candidates=candidates,
            message=message,
        )

    target_label = _node_path(nodes, node_id)
    chunks = await searching_for_chunks_with_node_id(
        node_id=node_id,
        doc_id=doc_id,
        user_id=user_id,
    )
    if not chunks:
        return DocumentSummaryContext(
            doc_id=doc_id,
            doc_name=doc_name,
            status="not_found",
            target_label=target_label,
            node_id=node_id,
            nodes=nodes,
            message=f"No chunks were found for {target_label}.",
        )

    if len(chunks) <= SMALL_MAX_CHUNKS:
        strategy = "direct"
        context = _format_chunks(chunks)
    elif len(chunks) <= MEDIUM_MAX_CHUNKS:
        strategy = "map_reduce"
        context = await _map_reduce_context(
            doc_name=doc_name,
            target_label=target_label,
            chunks=chunks,
            semaphore=semaphore,
        )
    else:
        strategy = "hierarchical"
        context = await _hierarchical_context(
            doc_name=doc_name,
            target_label=target_label,
            node_id=node_id,
            nodes=nodes,
            chunks=chunks,
            semaphore=semaphore,
        )

    return DocumentSummaryContext(
        doc_id=doc_id,
        doc_name=doc_name,
        status="ready",
        target_label=target_label,
        node_id=node_id,
        strategy=strategy,
        context=_truncate(context, MAX_CONTEXT_CHARS_PER_DOC),
        chunks=chunks,
        nodes=nodes,
    )


def _format_document_context(context: DocumentSummaryContext) -> str:
    chunk_count = len(context.chunks)
    return (
        f"<document name=\"{context.doc_name}\" id=\"{context.doc_id}\">\n"
        f"Scope: {context.target_label}\n"
        f"Strategy: {context.strategy}\n"
        f"Chunks used: {chunk_count} ({_page_label(context.chunks)})\n\n"
        f"{context.context}\n"
        "</document>"
    )


def _build_final_messages(
    *,
    question: str | None,
    target: str | None,
    contexts: list[DocumentSummaryContext],
) -> list[SystemMessage | HumanMessage]:
    multi_doc = len(contexts) > 1
    document_context = "\n\n".join(_format_document_context(item) for item in contexts)
    requested_target = target.strip() if target and target.strip() else "whole document"

    instructions = (
        "Create the final user-facing summary in Markdown.\n"
        "- Use only the supplied document context.\n"
        "- Start with a brief overview, then cover the key ideas and important details.\n"
        "- Preserve the PDF outline structure when it helps readability.\n"
        "- Be specific enough that the user can understand the section without rereading it.\n"
        "- Do not invent citations or facts."
    )
    if multi_doc:
        instructions += (
            "\n- Use a clear heading for each document and finish with a short "
            "combined takeaway across the selected PDFs."
        )

    return [
        SystemMessage(
            content=(
                "You are DocMind's outline-aware PDF summarization engine. "
                "You write clear, faithful summaries from retrieved PDF chunks "
                "and intermediate summaries."
            )
        ),
        HumanMessage(
            content=(
                f"User request: {question or 'Summarize the selected PDF content.'}\n"
                f"Requested target: {requested_target}\n\n"
                f"{instructions}\n\n"
                f"{document_context}"
            )
        ),
    ]


def _representative_chunks(
    chunks: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    if len(chunks) <= limit:
        return chunks
    if limit <= 1:
        return [chunks[0]]

    selected: list[dict[str, Any]] = []
    used_indices: set[int] = set()
    span = len(chunks) - 1
    for offset in range(limit):
        index = round((span * offset) / (limit - 1))
        if index in used_indices:
            continue
        selected.append(chunks[index])
        used_indices.add(index)
    return selected


def _build_sources(
    contexts: list[DocumentSummaryContext],
) -> tuple[list[Citation], list[DocumentContribution]]:
    citations: list[Citation] = []
    contributions: list[DocumentContribution] = []

    for context in contexts:
        selected_chunks = _representative_chunks(
            context.chunks,
            MAX_CITATIONS_PER_DOC,
        )
        citation_ids: list[str] = []
        relevant_pages: set[int] = set()

        for chunk in selected_chunks:
            page = _chunk_page_number(chunk)

            if page is not None:
                relevant_pages.add(page)

            citation_id = f"C{len(citations) + 1}"
            citation_ids.append(citation_id)
            citations.append(
                Citation(
                    citation_id=citation_id,
                    document_id=context.doc_id,
                    document_name=context.doc_name,
                    page_number=page,
                    chunk_id=str(chunk.get("id")) if chunk.get("id") is not None else None,
                    excerpt=_truncate(_clean_text(chunk.get("page_content")), 220),
                )
            )

        contributions.append(
            DocumentContribution(
                document_id=context.doc_id,
                document_name=context.doc_name,
                contribution=(
                    f"Summarized {context.target_label} from {len(context.chunks)} "
                    f"ordered chunks using {context.strategy.replace('_', '-')}."
                ),
                relevant_pages=sorted(relevant_pages),
                citation_ids=citation_ids,
            )
        )

    return citations, contributions


def _format_issue_answer(
    *,
    target: str | None,
    issues: list[DocumentSummaryContext],
) -> str:
    target_label = target.strip() if target and target.strip() else "that request"
    lines = [
        "I need one clarification before I can summarize this.",
        "",
    ]

    for issue in issues:
        lines.append(f"**{issue.doc_name}**")
        if issue.status == "ambiguous":
            lines.append(f'Multiple outline entries match "{target_label}":')
            for candidate in issue.candidates[:8]:
                lines.append(f"- {_candidate_label(candidate, issue.nodes)}")
            lines.append(
                "Reply with one of those node IDs, for example: "
                "`summarize node_12`."
            )
        else:
            lines.append(issue.message or f'I could not resolve "{target_label}".')
        lines.append("")

    return "\n".join(lines).strip()


async def stream_level1_pdf_with_outline(
    *,
    target: str | None,
    doc_ids: list[str],
    user_id: str,
    document_names: dict[str, str] | None = None,
    question: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Stream an outline-aware level-1 summary for one or more PDFs.

    This generator intentionally emits the same event types as the existing
    chat pipeline: status, token, citations, final, error, done.
    """
    answer_parts: list[str] = []
    document_names = document_names or {}
    unique_doc_ids = list(dict.fromkeys(doc_ids))

    try:
        if not unique_doc_ids:
            answer = "No selected PDF was available to summarize."
            yield {"type": "token", "content": answer}
            yield {
                "type": "final",
                "data": DocMindResponse(
                    answer=answer,
                    answer_found=False,
                    status=AnswerStatus.NOT_FOUND,
                ).model_dump(),
            }
            yield {"type": "done"}
            return

        semaphore = asyncio.Semaphore(MAX_PARALLEL_LLM_CALLS)

        yield {"type": "status", "message": "Resolving PDF outline target"}
        prepare_tasks = [
            _prepare_document_context(
                target=target,
                doc_id=doc_id,
                user_id=user_id,
                doc_name=document_names.get(doc_id, "document.pdf"),
                semaphore=semaphore,
            )
            for doc_id in unique_doc_ids
        ]
        contexts = await asyncio.gather(*prepare_tasks)

        issues = [context for context in contexts if context.status != "ready"]
        if issues:
            answer = _format_issue_answer(target=target, issues=issues)
            yield {"type": "token", "content": answer}
            yield {
                "type": "final",
                "data": DocMindResponse(
                    answer=answer,
                    answer_found=False,
                    status=AnswerStatus.PARTIAL,
                ).model_dump(),
            }
            yield {"type": "done"}
            return

        ready_contexts = [context for context in contexts if context.status == "ready"]
        citations, contributions = _build_sources(ready_contexts)

        yield {"type": "status", "message": "Generating summary"}
        messages = _build_final_messages(
            question=question,
            target=target,
            contexts=ready_contexts,
        )
        async for chunk in get_streaming_summary_llm().astream(messages):
            text = _message_text(chunk.content)
            if not text:
                continue
            answer_parts.append(text)
            yield {"type": "token", "content": text}

        answer = "".join(answer_parts).strip()
        if not answer:
            raise RuntimeError("The model returned an empty summary")

        yield {
            "type": "citations",
            "citations": [citation.model_dump() for citation in citations],
        }
        yield {
            "type": "final",
            "data": DocMindResponse(
                answer=answer,
                answer_found=True,
                status=AnswerStatus.COMPLETE,
                document_contributions=contributions,
                citations=citations,
                confidence_score=0.85,
                follow_up_questions=[],
            ).model_dump(),
        }
        yield {"type": "done"}

    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("level_1_pdf_with_outline pipeline failed")
        yield {
            "type": "error",
            "message": "Unable to generate the summary. Please try again.",
        }
        yield {"type": "done"}


async def level_1_pdf_with_outline(
    *,
    target: str | None,
    doc_id: str,
    user_id: str,
    document_name: str | None = None,
    question: str | None = None,
) -> DocMindResponse:
    """Convenience function for a single outline PDF when streaming is not needed."""
    final_data: dict[str, Any] | None = None
    async for event in stream_level1_pdf_with_outline(
        target=target,
        doc_ids=[doc_id],
        user_id=user_id,
        document_names={doc_id: document_name or "document.pdf"},
        question=question,
    ):
        if event.get("type") == "final":
            final_data = event["data"]

    if final_data is None:
        raise RuntimeError("The summarization pipeline did not produce a final response")
    return DocMindResponse(**final_data)
