from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Protocol

from qdrant_client import models

from .state import DataAnalysisRetrievalState
from .utils.hybrid_search import HybridQdrantSearcher
from .utils.limits import limits_for_scope
from .utils.query_sets import combine_queries
from .utils.sparse_index import text_sparse_collection_name


class AsyncTextRetriever(Protocol):
    async def retrieve(
        self, state: DataAnalysisRetrievalState
    ) -> list[dict[str, Any]]: ...


def _text_filter(*, user_id: str, document_ids: list[str]) -> models.Filter:
    if not user_id.strip() or not document_ids:
        raise ValueError("Text retrieval requires user_id and document_ids")
    return models.Filter(
        must=[
            models.FieldCondition(
                key="metadata.user_id",
                match=models.MatchValue(value=user_id),
            ),
            models.FieldCondition(
                key="metadata.doc_id",
                match=models.MatchAny(any=document_ids),
            ),
        ]
    )


class QdrantTextRetriever:
    def __init__(self, searcher: HybridQdrantSearcher | None = None) -> None:
        self._searcher = searcher or HybridQdrantSearcher()

    async def retrieve(
        self, state: DataAnalysisRetrievalState
    ) -> list[dict[str, Any]]:
        dense_collection = os.getenv("QDRANT_COLLECTION_NAME")
        if not dense_collection:
            raise RuntimeError("QDRANT_COLLECTION_NAME is not configured")
        queries = combine_queries(
            state["query"],
            state.get("shared_queries", []),
            state.get("text_queries", []),
        )
        document_ids = list(dict.fromkeys(state.get("document_ids", [])))
        limits = limits_for_scope(state.get("retrieval_scope", "normal"))
        hits = await self._searcher.search(
            queries=queries,
            dense_collection=dense_collection,
            sparse_collection=text_sparse_collection_name(dense_collection),
            query_filter=_text_filter(
                user_id=state["user_id"],
                document_ids=document_ids,
            ),
            candidate_limit=limits.text_candidates,
            final_limit=limits.final_text_chunks,
        )
        return [
            {
                "chunk_id": hit.point_id,
                "rrf_score": hit.score,
                "text": str(hit.payload.get("page_content") or ""),
                "metadata": dict(hit.payload.get("metadata") or {}),
                "matched_queries": hit.matched_queries,
                "retrieval_modes": hit.retrieval_modes,
            }
            for hit in hits
        ]


@lru_cache(maxsize=1)
def get_text_retriever() -> QdrantTextRetriever:
    return QdrantTextRetriever()


def build_text_retrieval_node(
    retriever: AsyncTextRetriever | None = None,
) -> Any:
    async def retrieve_text(
        state: DataAnalysisRetrievalState,
    ) -> dict[str, list[dict[str, Any]]]:
        selected = retriever or get_text_retriever()
        return {"retrieved_text_chunks": await selected.retrieve(state)}

    return retrieve_text
