from __future__ import annotations

import unittest

import numpy as np

from scripts.intention_pipelines.summarization_pipeline.summary_index import (
    _build_node_summary_indexes,
    initialize_pending_summary_indexes,
)


class SummaryIndexTests(unittest.TestCase):
    def test_pending_nodes_are_initialized_without_mutating_outline(self) -> None:
        nodes = [{"node_id": "node_1", "title": "One"}]

        initialized = initialize_pending_summary_indexes(nodes)

        self.assertNotIn("summary_index", nodes[0])
        self.assertEqual(initialized[0]["summary_index"]["status"], "pending")
        self.assertEqual(initialized[0]["summary_index"]["version"], "v1")

    def test_chunks_are_selected_independently_per_node(self) -> None:
        nodes = [
            {"node_id": "node_1", "title": "One"},
            {"node_id": "node_2", "title": "Two"},
        ]
        rng = np.random.default_rng(11)
        embedded_chunks = [
            (
                f"node_1_chunk_{index}",
                {"node_id": "node_1", "document_chunk_index": index},
                rng.normal(size=10).astype(np.float32),
            )
            for index in range(30)
        ] + [
            (
                f"node_2_chunk_{index}",
                {"node_id": "node_2", "document_chunk_index": 30 + index},
                rng.normal(size=10).astype(np.float32),
            )
            for index in range(5)
        ]

        indexed = _build_node_summary_indexes(
            nodes=nodes,
            embedded_chunks=embedded_chunks,
        )
        first = indexed[0]["summary_index"]
        second = indexed[1]["summary_index"]

        self.assertEqual(first["method"], "first_last_kmeans_mmr")
        self.assertEqual(first["chunk_count"], 30)
        self.assertEqual(first["representative_chunk_ids"][0], "node_1_chunk_0")
        self.assertEqual(first["representative_chunk_ids"][-1], "node_1_chunk_29")
        self.assertEqual(second["method"], "all_chunks")
        self.assertEqual(
            second["representative_chunk_ids"],
            [f"node_2_chunk_{index}" for index in range(5)],
        )


if __name__ == "__main__":
    unittest.main()
