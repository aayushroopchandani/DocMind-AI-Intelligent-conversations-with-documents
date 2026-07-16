from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts.intention_pipelines.summarization_pipeline import (
    level1_pdf_with_outline as pipeline,
)


def _nodes(status: str, *, chunk_count: int = 0) -> list[dict]:
    return [
        {
            "node_id": "node_1",
            "title": "Section One",
            "level": 1,
            "page_start": 1,
            "page_end": 20,
            "parent_id": None,
            "summary_index": {
                "status": status,
                "chunk_count": chunk_count,
                "representative_chunk_ids": ["chunk_0", "chunk_60"],
                "method": "first_last_kmeans_mmr",
                "cluster_count": 5,
                "version": "v1",
            },
        }
    ]


def _chunks(count: int) -> list[dict]:
    return [
        {
            "id": f"chunk_{index}",
            "page_content": f"content {index}",
            "metadata": {
                "node_id": "node_1",
                "document_chunk_index": index,
                "page_number": index + 1,
            },
        }
        for index in range(count)
    ]


class SummaryStrategyTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_root_cache_skips_retrieval_and_summarization(self) -> None:
        document = {
            "summary_index_status": "ready",
            "summary_index_version": "v1",
            "nodes": {"nodes": _nodes("ready", chunk_count=61)},
        }
        cached_response = {
            "answer": "Cached section summary",
            "answer_found": True,
            "status": "complete",
            "document_contributions": [],
            "citations": [],
            "confidence_score": 0.85,
            "follow_up_questions": [],
        }

        with (
            patch.object(
                pipeline.crud,
                "get_document_nodes",
                new=AsyncMock(return_value=document),
            ),
            patch.object(
                pipeline.crud,
                "get_cached_summary",
                new=AsyncMock(
                    return_value={
                        "response": cached_response,
                        "metrics": {"source_chunk_count": 61},
                    }
                ),
            ) as get_cached,
            patch.object(
                pipeline,
                "searching_for_chunks_by_ids",
                new=AsyncMock(),
            ) as retrieve_by_id,
            patch.object(
                pipeline,
                "searching_for_chunks_with_node_id",
                new=AsyncMock(),
            ) as retrieve_all,
        ):
            context = await pipeline._prepare_document_context(
                target=None,
                doc_id="doc",
                user_id="user",
                doc_name="doc.pdf",
                semaphore=asyncio.Semaphore(1),
                use_summary_cache=True,
            )

        self.assertEqual(context.strategy, "cached_summary")
        self.assertEqual(context.cached_response, cached_response)
        get_cached.assert_awaited_once()
        retrieve_by_id.assert_not_awaited()
        retrieve_all.assert_not_awaited()

    async def test_normal_large_summary_uses_only_representative_ids(self) -> None:
        document = {
            "summary_index_status": "ready",
            "summary_index_version": "v1",
            "nodes": {"nodes": _nodes("ready", chunk_count=61)},
        }
        representatives = [_chunks(61)[0], _chunks(61)[-1]]

        with (
            patch.object(
                pipeline.crud,
                "get_document_nodes",
                new=AsyncMock(return_value=document),
            ),
            patch.object(
                pipeline,
                "searching_for_chunks_by_ids",
                new=AsyncMock(return_value=representatives),
            ) as retrieve_by_id,
            patch.object(
                pipeline,
                "searching_for_chunks_with_node_id",
                new=AsyncMock(),
            ) as retrieve_all,
            patch.object(
                pipeline,
                "_representative_map_reduce_context",
                new=AsyncMock(return_value="representative notes"),
            ),
        ):
            context = await pipeline._prepare_document_context(
                target=None,
                doc_id="doc",
                user_id="user",
                doc_name="doc.pdf",
                semaphore=asyncio.Semaphore(1),
            )

        self.assertEqual(context.status, "ready")
        self.assertEqual(context.strategy, "representative_map_reduce")
        self.assertEqual(context.source_chunk_count, 61)
        retrieve_by_id.assert_awaited_once()
        retrieve_all.assert_not_awaited()

    async def test_pending_large_summary_never_uses_full_hierarchy(self) -> None:
        document = {
            "summary_index_status": "processing",
            "summary_index_version": "v1",
            "nodes": {"nodes": _nodes("pending")},
        }

        with (
            patch.object(
                pipeline.crud,
                "get_document_nodes",
                new=AsyncMock(return_value=document),
            ),
            patch.object(
                pipeline,
                "searching_for_chunks_with_node_id",
                new=AsyncMock(return_value=_chunks(61)),
            ),
            patch.object(
                pipeline,
                "_hierarchical_context",
                new=AsyncMock(),
            ) as full_hierarchy,
        ):
            context = await pipeline._prepare_document_context(
                target=None,
                doc_id="doc",
                user_id="user",
                doc_name="doc.pdf",
                semaphore=asyncio.Semaphore(1),
            )

        self.assertEqual(context.status, "summary_index_pending")
        self.assertIn("still being prepared", context.message)
        full_hierarchy.assert_not_awaited()

    async def test_deep_summary_can_use_full_hierarchy_while_index_is_pending(self) -> None:
        document = {
            "summary_index_status": "processing",
            "summary_index_version": "v1",
            "nodes": {"nodes": _nodes("pending")},
        }

        with (
            patch.object(
                pipeline.crud,
                "get_document_nodes",
                new=AsyncMock(return_value=document),
            ),
            patch.object(
                pipeline,
                "searching_for_chunks_with_node_id",
                new=AsyncMock(return_value=_chunks(61)),
            ),
            patch.object(
                pipeline,
                "_hierarchical_context",
                new=AsyncMock(return_value="deep notes"),
            ) as full_hierarchy,
        ):
            context = await pipeline._prepare_document_context(
                target=None,
                doc_id="doc",
                user_id="user",
                doc_name="doc.pdf",
                semaphore=asyncio.Semaphore(1),
                deep_summary=True,
            )

        self.assertEqual(context.status, "ready")
        self.assertEqual(context.strategy, "full_hierarchical")
        full_hierarchy.assert_awaited_once()

    async def test_stream_records_actual_llm_usage_in_cache_metrics(self) -> None:
        context = pipeline.DocumentSummaryContext(
            doc_id="doc",
            doc_name="doc.pdf",
            status="ready",
            strategy="representative_map_reduce",
            source_chunk_count=320,
            candidate_chunk_count=320,
            map_group_count=4,
            context="prepared notes",
            chunks=_chunks(2),
            scope_node_ids=["node_1"],
            cache_key={
                "user_id": "user",
                "document_id": "doc",
                "scope_root_node_id": "__document__",
                "mode": "standard",
                "summary_index_version": "v1",
                "prompt_version": "v1",
                "model": "model|utility=utility-model",
            },
        )

        class StreamingLLM:
            async def astream(self, messages):
                del messages
                yield SimpleNamespace(
                    content="Generated summary",
                    usage_metadata={
                        "input_tokens": 120,
                        "output_tokens": 18,
                    },
                    response_metadata={},
                )

        cache_write = AsyncMock()
        with (
            patch.object(
                pipeline.crud,
                "user_has_deep_summary_access",
                new=AsyncMock(return_value=False),
            ),
            patch.object(
                pipeline,
                "_prepare_document_context",
                new=AsyncMock(return_value=context),
            ),
            patch.object(
                pipeline,
                "get_streaming_summary_llm",
                return_value=StreamingLLM(),
            ),
            patch.object(
                pipeline.crud,
                "upsert_cached_summary",
                new=cache_write,
            ),
        ):
            events = [
                event
                async for event in pipeline.stream_level1_pdf_with_outline(
                    target=None,
                    doc_ids=["doc"],
                    user_id="user",
                    document_names={"doc": "doc.pdf"},
                )
            ]

        self.assertEqual(events[-1]["type"], "done")
        cache_write.assert_awaited_once()
        metrics = cache_write.await_args.kwargs["metrics"]
        self.assertEqual(metrics["llm_call_count"], 1)
        self.assertEqual(metrics["input_tokens"], 120)
        self.assertEqual(metrics["output_tokens"], 18)


if __name__ == "__main__":
    unittest.main()
