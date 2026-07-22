from __future__ import annotations

import asyncio
import unittest
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from scripts.data_analysis_agent.reterival.hybrid_retrieval_subgraph import (
    build_hybrid_retrieval_subgraph,
)
from scripts.data_analysis_agent.reterival.query_generation import (
    ConceptKind,
    GeneratedRetrievalQueries,
    MatchConcept,
    RetrievalScope,
    TableIntent,
)
from scripts.data_analysis_agent.reterival.state import create_retrieval_state
from scripts.data_analysis_agent.reterival.table_retrieval import _table_filter
from scripts.data_analysis_agent.reterival.text_retrieval import _text_filter
from scripts.data_analysis_agent.reterival.utils.hybrid_search import (
    HybridQdrantSearcher,
    reciprocal_rank_fusion,
)
from scripts.data_analysis_agent.reterival.utils.limits import limits_for_scope
from scripts.data_analysis_agent.reterival.utils.query_sets import combine_queries
from scripts.data_analysis_agent.reterival.utils.sparse_index import (
    SPARSE_VECTOR_NAME,
    get_sparse_encoder,
)


def _point(point_id: str, score: float) -> models.ScoredPoint:
    return models.ScoredPoint(
        id=point_id,
        version=1,
        score=score,
        payload={"page_content": point_id},
    )


class _FakeQueryGenerator:
    async def ainvoke(self, input: Any, **kwargs: Any) -> GeneratedRetrievalQueries:
        return GeneratedRetrievalQueries(
            retrieval_scope=RetrievalScope.NORMAL,
            shared_queries=["shared one", "shared two"],
            text_queries=["text one", "text two"],
            table_queries=["table one", "table two"],
            table_intent=TableIntent.REQUIRED,
            match_concepts=[
                MatchConcept(
                    canonical="revenue",
                    kind=ConceptKind.METRIC,
                )
            ],
            metrics=["revenue"],
            years=["2024"],
            column_terms=["revenue"],
        )


class _FakeDenseEmbeddings:
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class _ParallelProbe:
    def __init__(self) -> None:
        self.started: set[str] = set()
        self.both_started = asyncio.Event()

    async def run(self, name: str) -> None:
        self.started.add(name)
        if len(self.started) == 2:
            self.both_started.set()
        await asyncio.wait_for(self.both_started.wait(), timeout=1)


class _FakeTextRetriever:
    def __init__(self, probe: _ParallelProbe) -> None:
        self.probe = probe

    async def retrieve(self, state: Any) -> list[dict[str, Any]]:
        await self.probe.run("text")
        return [
            {
                "chunk_id": "chunk-1",
                "rrf_score": 0.1,
                "text": "Revenue for 2024 increased.",
                "metadata": {"doc_id": "d1", "page": 3},
                "matched_queries": ["compare revenue"],
                "retrieval_modes": ["dense", "sparse"],
            }
        ]


class _FakeTableRetriever:
    def __init__(self, probe: _ParallelProbe) -> None:
        self.probe = probe

    async def retrieve(self, state: Any) -> list[dict[str, Any]]:
        await self.probe.run("table")
        return [
            {
                "table_id": "table-1",
                "document_id": "d1",
                "rrf_score": 0.1,
                "title": "Revenue by year",
                "summary": "Annual revenue for 2024",
                "columns": ["year", "revenue"],
                "metrics": ["revenue"],
                "units": [],
                "keywords": [],
                "page_start": 3,
                "page_end": 3,
                "matched_queries": ["compare revenue"],
                "retrieval_modes": ["dense", "sparse"],
            }
        ]


class HybridRetrievalUnitTests(unittest.TestCase):
    def test_query_set_keeps_original_then_shared_then_specialized(self) -> None:
        self.assertEqual(
            combine_queries(
                "original query",
                ["shared one", "shared two"],
                ["shared one", "specific one"],
            ),
            ["original query", "shared one", "shared two", "specific one"],
        )

    def test_scope_limits_match_the_configured_policy(self) -> None:
        self.assertEqual(limits_for_scope("normal").final_text_chunks, 6)
        self.assertEqual(limits_for_scope("normal").final_tables, 4)
        self.assertEqual(limits_for_scope("broad").text_candidates, 35)
        self.assertEqual(limits_for_scope("broad").final_tables, 10)

    def test_rrf_fuses_all_lists_equally_and_deduplicates_points(self) -> None:
        results = reciprocal_rank_fusion(
            [
                ("dense", "query one", [_point("a", 0.9), _point("b", 0.8)]),
                ("sparse", "query one", [_point("b", 12), _point("a", 8)]),
                ("dense", "query two", [_point("a", 0.7), _point("c", 0.6)]),
            ],
            limit=3,
        )

        self.assertEqual([item.point_id for item in results], ["a", "b", "c"])
        self.assertEqual(results[0].retrieval_modes, ["dense", "sparse"])
        self.assertEqual(results[0].matched_queries, ["query one", "query two"])

    def test_filters_use_the_existing_payload_paths(self) -> None:
        text_filter = _text_filter(user_id="u1", document_ids=["d1", "d2"])
        table_filter = _table_filter(user_id="u1", document_ids=["d1", "d2"])

        self.assertEqual(
            [condition.key for condition in text_filter.must],
            ["metadata.user_id", "metadata.doc_id"],
        )
        self.assertEqual(
            [condition.key for condition in table_filter.must],
            ["user_id", "document_id"],
        )


class HybridRetrievalGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_hybrid_search_queries_dense_and_sparse_indexes(self) -> None:
        client = AsyncQdrantClient(":memory:")
        await client.create_collection(
            "dense",
            vectors_config=models.VectorParams(
                size=3,
                distance=models.Distance.COSINE,
            ),
        )
        await client.create_collection(
            "sparse",
            vectors_config={},
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: models.SparseVectorParams(
                    modifier=models.Modifier.IDF
                )
            },
        )
        payload = {
            "page_content": "Net income for FY2023",
            "metadata": {"user_id": "u1", "doc_id": "d1"},
        }
        await client.upsert(
            "dense",
            [models.PointStruct(id=1, vector=[1.0, 0.0, 0.0], payload=payload)],
        )
        await client.upsert(
            "sparse",
            [
                models.PointStruct(
                    id=1,
                    vector={
                        SPARSE_VECTOR_NAME: get_sparse_encoder().encode(
                            "Net income for FY2023"
                        )
                    },
                    payload=payload,
                )
            ],
        )

        try:
            results = await HybridQdrantSearcher(
                client=client,
                embeddings=_FakeDenseEmbeddings(),
            ).search(
                queries=["net income FY2023"],
                dense_collection="dense",
                sparse_collection="sparse",
                query_filter=_text_filter(user_id="u1", document_ids=["d1"]),
                per_query_limit=5,
                fusion_limit=3,
            )
        finally:
            await client.close()

        self.assertEqual([result.point_id for result in results], ["1"])
        self.assertEqual(results[0].retrieval_modes, ["dense", "sparse"])

    async def test_text_and_table_nodes_execute_in_parallel(self) -> None:
        probe = _ParallelProbe()
        graph = build_hybrid_retrieval_subgraph(
            query_generator=_FakeQueryGenerator(),
            text_retriever=_FakeTextRetriever(probe),
            table_retriever=_FakeTableRetriever(probe),
        )

        result = await graph.ainvoke(
            create_retrieval_state(
                user_id="u1",
                chat_id="c1",
                query="compare revenue",
                document_ids=["d1"],
            )
        )

        self.assertEqual(probe.started, {"text", "table"})
        self.assertEqual(result["retrieved_text_chunks"][0]["chunk_id"], "chunk-1")
        self.assertEqual(result["retrieved_tables"][0]["table_id"], "table-1")
        self.assertEqual(result["final_text_chunks"][0]["chunk_id"], "chunk-1")
        self.assertEqual(result["final_tables"][0]["table_id"], "table-1")
        self.assertIn("relevance_score", result["final_text_chunks"][0])
