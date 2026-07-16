from __future__ import annotations

import unittest

import numpy as np

from scripts.intention_pipelines.summarization_pipeline.scope_budget import (
    build_section_aware_groups,
    select_scope_representatives,
)


def _nodes(counts: list[int]) -> list[dict]:
    return [
        {
            "node_id": f"node_{index}",
            "title": f"Section {index}",
            "level": 1,
            "page_start": index + 1,
            "page_end": index + 1,
            "parent_id": None,
            "summary_index": {"chunk_count": count},
        }
        for index, count in enumerate(counts)
    ]


def _chunks(counts: list[int]) -> list[dict]:
    rng = np.random.default_rng(19)
    result: list[dict] = []
    document_index = 0
    for node_index, count in enumerate(counts):
        for chunk_index in range(count):
            result.append(
                {
                    "id": f"node_{node_index}_chunk_{chunk_index}",
                    "page_content": "content",
                    "embedding": rng.normal(size=12).astype(np.float32),
                    "metadata": {
                        "node_id": f"node_{node_index}",
                        "chunk_index": chunk_index,
                        "document_chunk_index": document_index,
                    },
                }
            )
            document_index += 1
    return result


class ScopeBudgetTests(unittest.TestCase):
    def test_many_small_nodes_are_capped_with_node_coverage(self) -> None:
        counts = [8] * 40
        candidates = _chunks(counts)

        selection = select_scope_representatives(
            chunks=candidates,
            nodes=_nodes(counts),
            budget=80,
        )

        self.assertEqual(selection.candidate_count, 320)
        self.assertEqual(len(selection.chunks), 80)
        self.assertEqual(set(selection.node_quotas.values()), {2})
        self.assertEqual(selection.chunks[0]["id"], candidates[0]["id"])
        self.assertEqual(selection.chunks[-1]["id"], candidates[-1]["id"])
        order = [chunk["metadata"]["document_chunk_index"] for chunk in selection.chunks]
        self.assertEqual(order, sorted(order))

    def test_larger_node_receives_next_available_position(self) -> None:
        counts = [7, 5, 8]
        selection = select_scope_representatives(
            chunks=_chunks(counts),
            nodes=_nodes(counts),
            budget=7,
        )

        self.assertEqual(selection.node_quotas["node_2"], 3)
        self.assertEqual(selection.node_quotas["node_0"], 2)
        self.assertEqual(selection.node_quotas["node_1"], 2)

    def test_section_groups_remain_bounded_and_ordered(self) -> None:
        counts = [2] * 40
        chunks = _chunks(counts)
        groups = build_section_aware_groups(
            chunks=chunks,
            nodes=_nodes(counts),
            scope_root_node_id=None,
            group_size=20,
            max_groups=5,
        )

        self.assertEqual(len(groups), 5)
        self.assertTrue(all(len(group.chunks) <= 20 for group in groups))
        flattened = [chunk["id"] for group in groups for chunk in group.chunks]
        self.assertEqual(flattened, [chunk["id"] for chunk in chunks])


if __name__ == "__main__":
    unittest.main()
