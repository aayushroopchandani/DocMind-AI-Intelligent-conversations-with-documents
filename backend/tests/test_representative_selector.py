from __future__ import annotations

import unittest

import numpy as np

from scripts.intention_pipelines.summarization_pipeline import representative_selector


class RepresentativeSelectorTests(unittest.TestCase):
    @staticmethod
    def _embeddings(chunk_count: int, dimensions: int = 12) -> np.ndarray:
        return np.random.default_rng(7).normal(
            size=(chunk_count, dimensions)
        ).astype(np.float32)

    def test_small_node_keeps_every_chunk_in_order(self) -> None:
        selection = representative_selector.select_representative_indices(
            self._embeddings(8)
        )

        self.assertEqual(selection.indices, list(range(8)))
        self.assertEqual(selection.method, "all_chunks")
        self.assertEqual(selection.cluster_count, 0)

    def test_kmeans_selection_keeps_endpoints_and_order(self) -> None:
        selection = representative_selector.select_representative_indices(
            self._embeddings(30)
        )

        self.assertEqual(selection.method, "first_last_kmeans_mmr")
        self.assertEqual(selection.indices[0], 0)
        self.assertEqual(selection.indices[-1], 29)
        self.assertEqual(selection.indices, sorted(selection.indices))
        self.assertEqual(
            len(selection.indices),
            representative_selector._representative_budget(30),
        )

    def test_very_large_node_uses_minibatch_and_bounded_budget(self) -> None:
        selection = representative_selector.select_representative_indices(
            self._embeddings(400)
        )

        self.assertEqual(
            selection.method,
            "first_last_minibatch_kmeans_mmr",
        )
        self.assertEqual(selection.indices[0], 0)
        self.assertEqual(selection.indices[-1], 399)
        self.assertEqual(
            len(selection.indices),
            representative_selector._representative_budget(400),
        )
        self.assertLessEqual(
            len(selection.indices),
            representative_selector.MAX_REPRESENTATIVE_CHUNKS,
        )

    def test_embeddings_are_l2_normalized(self) -> None:
        normalized = representative_selector.normalize_embeddings(
            self._embeddings(10)
        )
        np.testing.assert_allclose(
            np.linalg.norm(normalized, axis=1),
            np.ones(10),
            atol=1e-6,
        )

    def test_scope_mmr_preserves_mandatory_endpoints(self) -> None:
        indices = representative_selector.select_mmr_indices(
            self._embeddings(20),
            6,
            mandatory_indices={0, 19},
        )

        self.assertEqual(len(indices), 6)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 19)
        self.assertEqual(indices, sorted(indices))


if __name__ == "__main__":
    unittest.main()
