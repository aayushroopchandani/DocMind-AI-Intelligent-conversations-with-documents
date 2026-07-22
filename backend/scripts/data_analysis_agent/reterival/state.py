from __future__ import annotations

from typing import Any, Literal, Required, TypedDict

from langchain_core.runnables import RunnableConfig


class DataAnalysisRetrievalState(TypedDict, total=False):
    """Checkpoint-friendly state shared by data-analysis retrieval subgraphs."""

    user_id: Required[str]
    chat_id: Required[str]
    query: Required[str]
    document_ids: list[str]
    retrieval_scope: Literal["normal", "broad"]
    shared_queries: list[str]
    text_queries: list[str]
    table_queries: list[str]
    metrics: list[str]
    years: list[str]
    entities: list[str]
    units: list[str]
    column_terms: list[str]
    retrieved_text_chunks: list[dict[str, Any]]
    retrieved_tables: list[dict[str, Any]]
    final_text_chunks: list[dict[str, Any]]
    final_tables: list[dict[str, Any]]


def _required_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _unique_ids(values: list[str] | None) -> list[str]:
    return list(
        dict.fromkeys(
            value
            for item in values or []
            if (value := str(item or "").strip())
        )
    )


def create_retrieval_state(
    *,
    user_id: str,
    chat_id: str,
    query: str,
    document_ids: list[str] | None = None,
) -> DataAnalysisRetrievalState:
    """Create the minimal initial state required by the retrieval graph."""

    return DataAnalysisRetrievalState(
        user_id=_required_text(user_id, field="user_id"),
        chat_id=_required_text(chat_id, field="chat_id"),
        query=_required_text(query, field="query"),
        document_ids=_unique_ids(document_ids),
        shared_queries=[],
        text_queries=[],
        table_queries=[],
        metrics=[],
        years=[],
        entities=[],
        units=[],
        column_terms=[],
        retrieved_text_chunks=[],
        retrieved_tables=[],
        final_text_chunks=[],
        final_tables=[],
    )


def retrieval_thread_config(*, chat_id: str, user_id: str) -> RunnableConfig:
    """Use the globally unique Mongo chat id as the LangGraph thread id."""

    normalized_chat_id = _required_text(chat_id, field="chat_id")
    normalized_user_id = _required_text(user_id, field="user_id")
    return {
        "configurable": {"thread_id": normalized_chat_id},
        "metadata": {
            "chat_id": normalized_chat_id,
            "user_id": normalized_user_id,
            "agent": "data_analysis",
        },
    }
