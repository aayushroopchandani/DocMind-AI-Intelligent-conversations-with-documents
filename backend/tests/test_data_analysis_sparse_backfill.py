from __future__ import annotations

import os
import unittest
import warnings
from unittest.mock import patch

from qdrant_client import QdrantClient, models

from scripts.data_analysis_agent.retrieval.utils.sparse_backfill import (
    SparseBackfillSpec,
    backfill_data_analysis_sparse_indexes,
    backfill_sparse_collection,
)
from scripts.data_analysis_agent.retrieval.utils.sparse_index import (
    SPARSE_VECTOR_NAME,
    get_sparse_encoder,
)


def _create_dense_collection(client: QdrantClient, name: str) -> None:
    client.create_collection(
        collection_name=name,
        vectors_config=models.VectorParams(
            size=3,
            distance=models.Distance.COSINE,
        ),
    )


class SparseBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = QdrantClient(":memory:")

    def tearDown(self) -> None:
        self.client.close()

    def test_backfill_is_batched_lexical_and_idempotent(self) -> None:
        _create_dense_collection(self.client, "dense")
        self.client.upsert(
            collection_name="dense",
            points=[
                models.PointStruct(
                    id=1,
                    vector=[1.0, 0.0, 0.0],
                    payload={
                        "page_content": "Revenue increased in fiscal 2024",
                        "metadata": {"user_id": "u1", "doc_id": "d1"},
                    },
                ),
                models.PointStruct(
                    id=2,
                    vector=[0.0, 1.0, 0.0],
                    payload={
                        "page_content": "Operating expenses declined",
                        "metadata": {"user_id": "u1", "doc_id": "d1"},
                    },
                ),
                models.PointStruct(
                    id=3,
                    vector=[0.0, 0.0, 1.0],
                    payload={"page_content": "   ", "metadata": {}},
                ),
            ],
            wait=True,
        )
        spec = SparseBackfillSpec(
            source_collection="dense",
            target_collection="dense__sparse",
            text_payload_field="page_content",
            payload_indexes=(),
        )

        first = backfill_sparse_collection(
            spec,
            client=self.client,
            batch_size=2,
        )
        second = backfill_sparse_collection(
            spec,
            client=self.client,
            batch_size=2,
        )

        self.assertEqual(first.scanned_count, 3)
        self.assertEqual(first.indexed_count, 2)
        self.assertEqual(first.skipped_count, 1)
        self.assertEqual(first.batch_count, 2)
        self.assertEqual(second, first)
        self.assertEqual(
            self.client.count(
                collection_name="dense__sparse",
                exact=True,
            ).count,
            2,
        )
        result = self.client.query_points(
            collection_name="dense__sparse",
            query=get_sparse_encoder().encode("fiscal revenue 2024"),
            using=SPARSE_VECTOR_NAME,
            limit=2,
            with_payload=True,
        )
        self.assertEqual([point.id for point in result.points], [1])
        self.assertEqual(
            result.points[0].payload["metadata"]["doc_id"],
            "d1",
        )

    def test_data_analysis_backfill_populates_both_companions(self) -> None:
        _create_dense_collection(self.client, "text_dense")
        _create_dense_collection(self.client, "structured_tables")
        self.client.upsert(
            collection_name="text_dense",
            points=[
                models.PointStruct(
                    id=1,
                    vector=[1.0, 0.0, 0.0],
                    payload={
                        "page_content": "Net income for 2024",
                        "metadata": {"user_id": "u1", "doc_id": "d1"},
                    },
                )
            ],
            wait=True,
        )
        self.client.upsert(
            collection_name="structured_tables",
            points=[
                models.PointStruct(
                    id=2,
                    vector=[0.0, 1.0, 0.0],
                    payload={
                        "table_id": "t1",
                        "document_id": "d1",
                        "user_id": "u1",
                        "summary": "Annual net income table for 2024",
                    },
                )
            ],
            wait=True,
        )

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Payload indexes have no effect in the local Qdrant.*",
            )
            with patch.dict(
                os.environ,
                {
                    "DATA_ANALYSIS_TEXT_SPARSE_COLLECTION": "text_lexical",
                    "DATA_ANALYSIS_TABLE_SPARSE_COLLECTION": "table_lexical",
                },
            ):
                results = backfill_data_analysis_sparse_indexes(
                    client=self.client,
                    text_collection="text_dense",
                    batch_size=1,
                )

        self.assertEqual(
            [result.target_collection for result in results],
            ["text_lexical", "table_lexical"],
        )
        self.assertEqual([result.indexed_count for result in results], [1, 1])
        self.assertTrue(
            self.client.collection_exists(collection_name="text_lexical")
        )
        self.assertTrue(
            self.client.collection_exists(collection_name="table_lexical")
        )

    def test_missing_dense_collection_is_rejected(self) -> None:
        spec = SparseBackfillSpec(
            source_collection="missing",
            target_collection="missing__sparse",
            text_payload_field="page_content",
            payload_indexes=(),
        )

        with self.assertRaisesRegex(RuntimeError, "does not exist"):
            backfill_sparse_collection(spec, client=self.client)


if __name__ == "__main__":
    unittest.main()
