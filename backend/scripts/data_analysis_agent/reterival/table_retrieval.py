from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol

from qdrant_client import models

from .state import DataAnalysisRetrievalState
from .utils.collections import STRUCTURED_TABLES_COLLECTION
from .utils.hybrid_search import HybridQdrantSearcher
from .utils.limits import limits_for_scope
from .utils.query_sets import combine_queries
from .utils.sparse_index import table_sparse_collection_name


class AsyncTableRetriever(Protocol):
    async def retrieve(
        self, state: DataAnalysisRetrievalState
    ) -> list[dict[str, Any]]: ...


def _table_filter(*, user_id: str, document_ids: list[str]) -> models.Filter:
    if not user_id.strip() or not document_ids:
        raise ValueError("Table retrieval requires user_id and document_ids")
    return models.Filter(
        must=[
            models.FieldCondition(
                key="user_id",
                match=models.MatchValue(value=user_id),
            ),
            models.FieldCondition(
                key="document_id",
                match=models.MatchAny(any=document_ids),
            ),
        ]
    )


class QdrantTableRetriever:
    def __init__(self, searcher: HybridQdrantSearcher | None = None) -> None:
        self._searcher = searcher or HybridQdrantSearcher()

    async def retrieve(
        self, state: DataAnalysisRetrievalState
    ) -> list[dict[str, Any]]:
        queries = combine_queries(
            state["query"],
            state.get("shared_queries", []),
            state.get("table_queries", []),
        )
        document_ids = list(dict.fromkeys(state.get("document_ids", [])))
        limits = limits_for_scope(state.get("retrieval_scope", "normal"))
        hits = await self._searcher.search(
            queries=queries,
            dense_collection=STRUCTURED_TABLES_COLLECTION,
            sparse_collection=table_sparse_collection_name(),
            query_filter=_table_filter(
                user_id=state["user_id"],
                document_ids=document_ids,
            ),
            candidate_limit=limits.table_candidates,
            final_limit=limits.final_tables,
        )
        return [
            {
                "point_id": hit.point_id,
                "table_id": str(hit.payload.get("table_id") or ""),
                "document_id": str(hit.payload.get("document_id") or ""),
                "rrf_score": hit.score,
                "title": str(hit.payload.get("title") or ""),
                "summary": str(hit.payload.get("summary") or ""),
                "columns": list(hit.payload.get("columns") or []),
                "metrics": list(hit.payload.get("metrics") or []),
                "units": list(hit.payload.get("units") or []),
                "page_start": hit.payload.get("page_start"),
                "page_end": hit.payload.get("page_end"),
                "matched_queries": hit.matched_queries,
                "retrieval_modes": hit.retrieval_modes,
            }
            for hit in hits
        ]


@lru_cache(maxsize=1)
def get_table_retriever() -> QdrantTableRetriever:
    return QdrantTableRetriever()


def build_table_retrieval_node(
    retriever: AsyncTableRetriever | None = None,
) -> Any:
    async def retrieve_tables(
        state: DataAnalysisRetrievalState,
    ) -> dict[str, list[dict[str, Any]]]:
        selected = retriever or get_table_retriever()
        return {"retrieved_tables": await selected.retrieve(state)}

    return retrieve_tables
