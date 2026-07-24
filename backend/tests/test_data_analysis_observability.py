from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.data_analysis_agent.analysis.observability import (
    record_analysis_trace,
)
from scripts.data_analysis_agent.analysis.state import (
    AnalysisPhase,
    analysis_thread_config,
    create_analysis_state,
)


DOCUMENT_ID = "a" * 64


class _FakeRun:
    def __init__(self, parent_run: "_FakeRun | None" = None) -> None:
        self.parent_run = parent_run
        self.metadata: dict[str, object] = {}
        self.tags: list[str] = []

    def add_metadata(self, metadata: dict[str, object]) -> None:
        self.metadata.update(metadata)

    def add_tags(self, tags: tuple[str, ...]) -> None:
        self.tags.extend(tags)


class AnalysisObservabilityTests(unittest.TestCase):
    def test_trace_metrics_are_added_to_node_and_root_runs(self) -> None:
        root = _FakeRun()
        node = _FakeRun(parent_run=root)

        with patch(
            "scripts.data_analysis_agent.analysis.observability."
            "_get_current_run_tree",
            return_value=node,
        ):
            recorded = record_analysis_trace(
                metrics={
                    "decision": "ready",
                    "phase": AnalysisPhase.ASSESSED,
                    "counts": tuple(range(30)),
                },
                tags=("data-analysis", "readiness:ready", "data-analysis"),
            )

        self.assertTrue(recorded)
        self.assertEqual(node.metadata["decision"], "ready")
        self.assertEqual(node.metadata["phase"], "assessed")
        self.assertEqual(len(node.metadata["counts"]), 20)
        self.assertEqual(root.metadata, node.metadata)
        self.assertEqual(
            node.tags,
            ["data-analysis", "readiness:ready"],
        )
        self.assertEqual(root.tags, node.tags)

    def test_trace_recording_is_a_noop_without_an_active_run(self) -> None:
        with patch(
            "scripts.data_analysis_agent.analysis.observability."
            "_get_current_run_tree",
            return_value=None,
        ):
            self.assertFalse(
                record_analysis_trace(metrics={"decision": "ready"})
            )

    def test_parent_config_contains_langsmith_run_identity(self) -> None:
        state = create_analysis_state(
            user_id="user-1",
            chat_id="chat-1",
            query="compare revenue",
            document_ids=[DOCUMENT_ID],
        )

        config = analysis_thread_config(state)

        self.assertEqual(config["run_name"], "data_analysis_agent")
        self.assertIn("data-analysis", config["tags"])
        self.assertEqual(
            config["metadata"]["selected_document_count"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
