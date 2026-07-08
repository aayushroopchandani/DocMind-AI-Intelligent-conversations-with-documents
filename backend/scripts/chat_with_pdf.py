"""
End-to-end multi-document RAG pipeline for DocMind.

`ask_question()` is an async generator that drives one question/answer
exchange and yields UI events the API layer forwards as Server-Sent Events:

    status  -> progress messages while retrieval runs
    token   -> streamed answer text
    citations / final -> structured enrichment after streaming
    error / done

Flow:
    load chat context -> rewrite standalone retrieval query ->
    MultiQueryRetriever (Qdrant, filtered to the chat's PDFs) ->
    dedupe -> per-document balancing + token budget -> citation IDs ->
    stream answer -> structured DocMindResponse -> persist messages + summary.
"""

import asyncio
import logging
import os
import re
import sys

# Add parent directory of scripts to sys.path to allow importing backend modules
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from typing import AsyncGenerator, Optional

from dotenv import load_dotenv
from langchain_classic.retrievers.multi_query import MultiQueryRetriever
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from config.settings import settings
from db import crud
from qdrant_manager import get_client, get_vector_store
from utils.format_document import (
    balance_documents,
    deduplicate_documents,
    format_documents_with_citations,
)
from utils.prompts import (
    get_human_message,
    get_metadata_human_message,
    get_metadata_system_message,
    get_rewrite_human_message,
    get_rewrite_system_message,
    get_summary_human_message,
    get_summary_system_message,
    get_system_message,
)
from utils.pydantic_schemas import (
    AnswerStatus,
    ChatRequest,
    Citation,
    DocMindResponse,
    DocumentContribution,
    ResponseMetadata,
)

load_dotenv()

logger = logging.getLogger(__name__)

NOT_FOUND_PHRASE = "I could not find this information in the selected documents."

# embedding model
embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    dimensions=1536,
)

# Main answering model (streams the answer).
main_llm = ChatOpenAI(
    model="google/gemini-2.5-flash",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    # temperature=0.2,
    streaming=True,
)

# Cheap/fast model for query rewriting, multi-query generation and summaries.
utility_llm = ChatOpenAI(
    model="google/gemini-2.5-flash-lite",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    temperature=0,
)

metadata_llm = utility_llm.with_structured_output(ResponseMetadata)


# --------------------------------------------------------------------------- #
# Conversation memory helpers
# --------------------------------------------------------------------------- #
def format_recent_conversation(messages: list[dict]) -> str:
    """Render the last few DB messages as plain dialogue for prompts."""
    lines = []
    for message in messages:
        role = "User" if message.get("role") == "user" else "Assistant"
        content = (message.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def rewrite_standalone_question(
    question: str, summary: str, recent_conversation: str
) -> str:
    """
    Turn a follow-up ("does it apply to interns?") into a standalone retrieval
    query. Used ONLY for retrieval — the original question is still what the
    final LLM answers, so the reply stays conversational.
    """
    if not summary and not recent_conversation:
        return question

    try:
        result = await utility_llm.ainvoke(
            [
                SystemMessage(content=get_rewrite_system_message()),
                HumanMessage(
                    content=get_rewrite_human_message(
                        question, summary, recent_conversation
                    )
                ),
            ]
        )
        rewritten = (result.content or "").strip().strip('"')
        return rewritten or question
    except Exception:
        logger.exception("Standalone-question rewrite failed; using raw question")
        return question


async def update_chat_summary(chat: dict, user_id: str) -> None:
    """
    Refresh the rolling summary once enough new messages accumulated.
    Called after each exchange is persisted; cheap no-op otherwise.
    """
    conversation = chat.get("conversation", [])
    memory = chat.get("memory") or {}
    summarized_count = int(memory.get("summarized_count", 0))
    total = len(conversation)

    if total - summarized_count < settings.memory_summary_every:
        return

    # Summarize everything except the freshest window (which stays verbatim).
    keep_verbatim = settings.memory_recent_messages
    to_summarize = conversation[summarized_count : max(summarized_count, total - keep_verbatim)]
    if not to_summarize:
        return

    new_messages_text = format_recent_conversation(to_summarize)
    try:
        result = await utility_llm.ainvoke(
            [
                SystemMessage(content=get_summary_system_message()),
                HumanMessage(
                    content=get_summary_human_message(
                        memory.get("summary", ""), new_messages_text
                    )
                ),
            ]
        )
        new_summary = (result.content or "").strip()
        if new_summary:
            await crud.update_chat_memory(
                chat_id=chat["id"],
                user_id=user_id,
                summary=new_summary,
                summarized_count=summarized_count + len(to_summarize),
            )
    except Exception:
        logger.exception("Rolling summary update failed (will retry next exchange)")


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #
def create_multi_query_retriever(user_id: str, document_ids: list[str]) -> MultiQueryRetriever:
    """Qdrant retriever locked to this user's selected documents, wrapped in MultiQuery."""
    collection_name = os.getenv("QDRANT_COLLECTION_NAME")
    client = get_client()
    vector_store = get_vector_store(collection_name, embeddings, client)

    candidates = settings.retrieval_candidates_per_doc * max(1, len(document_ids))
    base_retriever = vector_store.as_retriever(
        search_kwargs={
            "k": candidates,
            "filter": Filter(
                must=[
                    FieldCondition(
                        key="metadata.user_id",
                        match=MatchValue(value=user_id),
                    ),
                    FieldCondition(
                        key="metadata.doc_id",
                        match=MatchAny(any=document_ids),
                    ),
                ]
            ),
        }
    )

    return MultiQueryRetriever.from_llm(retriever=base_retriever, llm=utility_llm)


async def retrieve_documents(retriever: MultiQueryRetriever, query: str) -> list:
    """Run multi-query retrieval, then dedupe + balance down to the final context set."""
    documents = await retriever.ainvoke(query)
    documents = deduplicate_documents(documents)
    return balance_documents(
        documents,
        max_total=settings.retrieval_final_chunks,
        max_per_doc=settings.retrieval_max_per_doc,
    )


# --------------------------------------------------------------------------- #
# Structured response building
# --------------------------------------------------------------------------- #
def _extract_used_citations(answer: str, citations: list[Citation]) -> list[Citation]:
    """Keep only the citations whose [Cn] markers actually appear in the answer."""
    used_ids = {f"C{n}" for n in re.findall(r"\[C(\d+)\]", answer)}
    used = [c for c in citations if c.citation_id in used_ids]
    return used


def _build_contributions(citations: list[Citation]) -> list[DocumentContribution]:
    """Deterministic per-document rollup derived purely from cited chunks."""
    by_doc: dict[str, DocumentContribution] = {}
    for citation in citations:
        entry = by_doc.get(citation.document_id)
        if entry is None:
            entry = DocumentContribution(
                document_id=citation.document_id,
                document_name=citation.document_name,
                contribution="",
            )
            by_doc[citation.document_id] = entry
        if citation.page_number is not None and citation.page_number not in entry.relevant_pages:
            entry.relevant_pages.append(citation.page_number)
        entry.citation_ids.append(citation.citation_id)

    for entry in by_doc.values():
        entry.relevant_pages.sort()
        pages = ", ".join(str(p) for p in entry.relevant_pages)
        entry.contribution = (
            f"Provided {len(entry.citation_ids)} cited passage(s)"
            + (f" from page(s) {pages}" if pages else "")
        )
    return list(by_doc.values())


async def build_structured_response(
    question: str,
    answer: str,
    used_citations: list[Citation],
) -> DocMindResponse:
    """
    Assemble the final DocMindResponse. Citations and contributions are derived
    deterministically from retrieval metadata; one lightweight structured call
    refines confidence, status, follow-ups and contribution wording. Any
    failure falls back to safe heuristics — the streamed answer is never lost.
    """
    not_found = NOT_FOUND_PHRASE.lower()[:40] in answer.lower()
    contributions = [] if not_found else _build_contributions(used_citations)

    response = DocMindResponse(
        answer=answer,
        answer_found=not not_found,
        status=AnswerStatus.NOT_FOUND if not_found else AnswerStatus.COMPLETE,
        citations=[] if not_found else used_citations,
        document_contributions=contributions,
        confidence_score=None,
        follow_up_questions=[],
    )

    try:
        citations_summary = "\n".join(
            f"[{c.citation_id}] doc_id={c.document_id} | {c.document_name}"
            + (f" | page {c.page_number}" if c.page_number is not None else "")
            for c in used_citations
        ) or "(none)"

        metadata: ResponseMetadata = await asyncio.wait_for(
            metadata_llm.ainvoke(
                [
                    SystemMessage(content=get_metadata_system_message()),
                    HumanMessage(
                        content=get_metadata_human_message(
                            question, answer, citations_summary
                        )
                    ),
                ]
            ),
            timeout=15,
        )

        response.answer_found = metadata.answer_found and not not_found
        response.status = metadata.status
        response.confidence_score = metadata.confidence_score
        response.follow_up_questions = metadata.follow_up_questions[:3]

        # Only trust LLM contribution *text*; ids/pages stay deterministic.
        llm_text = {c.document_id: c.contribution for c in metadata.document_contributions}
        for entry in response.document_contributions:
            if llm_text.get(entry.document_id):
                entry.contribution = llm_text[entry.document_id]
    except Exception:
        logger.exception("Metadata enrichment failed; returning heuristic response")
        response.confidence_score = 0.75 if used_citations else (0.2 if not not_found else None)

    return response


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
async def save_chat_messages(
    request: ChatRequest,
    answer: str,
    response: Optional[DocMindResponse],
    cancelled: bool = False,
) -> None:
    """Persist the exchange once (never per token) and refresh the summary."""
    meta: dict = {"cancelled": cancelled}
    if response is not None:
        meta.update(response.model_dump(exclude={"answer"}))

    chat = await crud.append_conversation_messages(
        chat_id=request.chat_id,
        user_id=request.user_id,
        messages=[
            {"role": "user", "content": request.question},
            {"role": "assistant", "content": answer, "meta": meta},
        ],
    )
    if chat is not None:
        await update_chat_summary(chat, request.user_id)


# --------------------------------------------------------------------------- #
# Main pipeline
# --------------------------------------------------------------------------- #
async def ask_question(request: ChatRequest) -> AsyncGenerator[dict, None]:
    """
    Answer one question against the chat's PDFs, yielding streaming UI events.
    The API layer converts each yielded dict into one SSE `data:` frame.
    """
    answer_parts: list[str] = []
    used_citations: list[Citation] = []
    response: Optional[DocMindResponse] = None

    try:
        # 1) Conversation context (summary + recent turns) for follow-ups.
        yield {"type": "status", "message": "Understanding your question"}
        recent_conversation = format_recent_conversation(request.recent_messages)
        retrieval_query = await rewrite_standalone_question(
            request.question, request.summary, recent_conversation
        )

        # 2) Multi-query retrieval over the chat's documents only.
        yield {"type": "status", "message": "Searching the selected documents"}
        retriever = create_multi_query_retriever(request.user_id, request.document_ids)
        documents = await retrieve_documents(retriever, retrieval_query)

        if not documents:
            answer = NOT_FOUND_PHRASE
            yield {"type": "token", "content": answer}
            response = DocMindResponse(
                answer=answer,
                answer_found=False,
                status=AnswerStatus.NOT_FOUND,
            )
            yield {"type": "final", "data": response.model_dump()}
            await save_chat_messages(request, answer, response)
            yield {"type": "done"}
            return

        # 3) Build the citation-tagged context within the token budget.
        yield {"type": "status", "message": "Preparing the answer"}
        context, citations = format_documents_with_citations(
            documents,
            document_names=request.document_names,
            max_context_tokens=settings.retrieval_max_context_tokens,
        )

        messages = [
            SystemMessage(content=get_system_message()),
            HumanMessage(
                content=get_human_message(
                    formatted_docs=context,
                    question=request.question,
                    citation_ids=[c.citation_id for c in citations],
                    chat_summary=request.summary,
                    recent_conversation=recent_conversation,
                    retrieval_query=retrieval_query,
                )
            ),
        ]

        # 4) Stream the answer while collecting it for persistence.
        yield {"type": "status", "message": "Generating response"}
        async for chunk in main_llm.astream(messages):
            text = chunk.content
            if text:
                answer_parts.append(text)
                yield {"type": "token", "content": text}

        answer = "".join(answer_parts).strip()
        if not answer:
            raise RuntimeError("The model returned an empty answer")

        # 5) Enrich: citations actually used + structured metadata.
        used_citations = _extract_used_citations(answer, citations)
        yield {
            "type": "citations",
            "citations": [c.model_dump() for c in used_citations],
        }

        response = await build_structured_response(request.question, answer, used_citations)
        yield {"type": "final", "data": response.model_dump()}

        # 6) Persist the exchange and refresh the rolling summary.
        await save_chat_messages(request, answer, response)
        yield {"type": "done"}

    except asyncio.CancelledError:
        # Client disconnected mid-stream: keep whatever was generated.
        partial = "".join(answer_parts).strip()
        if partial:
            try:
                await asyncio.shield(
                    save_chat_messages(request, partial, None, cancelled=True)
                )
            except Exception:
                logger.exception("Failed to persist partial answer after disconnect")
        raise

    except Exception:
        logger.exception("ask_question pipeline failed")
        yield {"type": "error", "message": "Unable to generate the answer. Please try again."}
        yield {"type": "done"}
