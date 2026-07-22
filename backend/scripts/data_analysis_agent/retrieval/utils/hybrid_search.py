from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from qdrant_client import AsyncQdrantClient, models

import qdrant_manager
from utils.embeddings import get_chunk_embedding

from .sparse_index import SPARSE_VECTOR_NAME, get_sparse_encoder


class AsyncDenseEmbeddings(Protocol):
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(slots=True)
class FusedPoint:
    point_id: str
    score: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)
    matched_queries: list[str] = field(default_factory=list)
    retrieval_modes: list[str] = field(default_factory=list)


def reciprocal_rank_fusion(
    ranked_lists: Sequence[tuple[str, str, Sequence[models.ScoredPoint]]],
    *,
    limit: int,
    rank_constant: int = 60,
) -> list[FusedPoint]:
    """Fuse dense/sparse results from every query with equal RRF influence."""

    if limit <= 0 or rank_constant < 0:
        raise ValueError("RRF limit must be positive and rank_constant non-negative")
    fused: dict[str, FusedPoint] = {}
    for mode, query, points in ranked_lists:
        seen_in_list: set[str] = set()
        for rank, point in enumerate(points, start=1):
            point_id = str(point.id)
            if point_id in seen_in_list:
                continue
            seen_in_list.add(point_id)
            item = fused.setdefault(
                point_id,
                FusedPoint(
                    point_id=point_id,
                    payload=dict(point.payload or {}),
                ),
            )
            item.score += 1.0 / (rank_constant + rank)
            if query not in item.matched_queries:
                item.matched_queries.append(query)
            if mode not in item.retrieval_modes:
                item.retrieval_modes.append(mode)
            if not item.payload and point.payload:
                item.payload = dict(point.payload)
    return sorted(
        fused.values(),
        key=lambda item: (-item.score, item.point_id),
    )[:limit]


class HybridQdrantSearcher:
    def __init__(
        self,
        *,
        client: AsyncQdrantClient | None = None,
        embeddings: AsyncDenseEmbeddings | None = None,
    ) -> None:
        self._client = client
        self._embeddings = embeddings

    @property
    def client(self) -> AsyncQdrantClient:
        return self._client or qdrant_manager.get_async_client()

    @property
    def embeddings(self) -> AsyncDenseEmbeddings:
        return self._embeddings or get_chunk_embedding()

    async def search(
        self,
        *,
        queries: Sequence[str],
        dense_collection: str,
        sparse_collection: str,
        query_filter: models.Filter,
        per_query_limit: int,
        fusion_limit: int,
    ) -> list[FusedPoint]:
        if not queries:
            return []
        if per_query_limit <= 0 or fusion_limit <= 0:
            raise ValueError("Hybrid retrieval limits must be positive")
        qdrant = self.client
        dense_vectors = await self.embeddings.aembed_documents(list(queries))
        sparse_vectors = get_sparse_encoder().encode_many(list(queries))

        dense_task = qdrant.query_batch_points(
            collection_name=dense_collection,
            requests=[
                models.QueryRequest(
                    query=vector,
                    filter=query_filter,
                    limit=per_query_limit,
                    with_payload=True,
                    with_vector=False,
                )
                for vector in dense_vectors
            ],
        )
        sparse_task = qdrant.query_batch_points(
            collection_name=sparse_collection,
            requests=[
                models.QueryRequest(
                    query=vector,
                    using=SPARSE_VECTOR_NAME,
                    filter=query_filter,
                    limit=per_query_limit,
                    with_payload=True,
                    with_vector=False,
                )
                for vector in sparse_vectors
            ],
        )
        dense_responses, sparse_responses = await asyncio.gather(
            dense_task,
            sparse_task,
        )
        ranked_lists = [
            ("dense", query, response.points)
            for query, response in zip(queries, dense_responses, strict=True)
        ] + [
            ("sparse", query, response.points)
            for query, response in zip(queries, sparse_responses, strict=True)
        ]
        return reciprocal_rank_fusion(ranked_lists, limit=fusion_limit)
