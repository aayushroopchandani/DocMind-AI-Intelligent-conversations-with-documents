from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from scripts.data_analysis_agent.runtime.models import (
    AnalysisEventType,
    AnalysisMode,
    AnalysisRun,
    AnalysisRunEvent,
    AnalysisRunOutcome,
    AnalysisRunPhase,
    AnalysisRunStatus,
)
from scripts.data_analysis_agent.runtime.services.event_stream import (
    EventStreamConfig,
    replayable_event_stream,
)


_NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


def _run(*, terminal: bool, sequence: int) -> AnalysisRun:
    values: dict[str, object] = {
        "run_id": str(uuid4()),
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "chat_id": "chat-1",
        "idempotency_key": "event-stream-key",
        "request_fingerprint": "e" * 64,
        "mode": AnalysisMode.ANALYSE,
        "prompt": "Prepare the selected data.",
        "version": sequence,
        "last_event_sequence": sequence,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    if terminal:
        values.update(
            {
                "status": AnalysisRunStatus.SUCCEEDED,
                "phase": AnalysisRunPhase.COMPLETED,
                "outcome": AnalysisRunOutcome.DATASETS_PREPARED,
                "started_at": _NOW,
                "completed_at": _NOW,
            }
        )
    return AnalysisRun.model_validate(values)


def _events(run: AnalysisRun, count: int) -> tuple[AnalysisRunEvent, ...]:
    return tuple(
        AnalysisRunEvent(
            run_id=run.run_id,
            user_id=run.user_id,
            workspace_id=run.workspace_id,
            sequence=index,
            event_type=(
                AnalysisEventType.RUN_COMPLETED
                if index == count
                else AnalysisEventType.DATASETS_PROFILED
            ),
            status=run.status if index == count else AnalysisRunStatus.ACTIVE,
            phase=(
                AnalysisRunPhase.COMPLETED
                if index == count
                else AnalysisRunPhase.EVIDENCE_PREPARATION
            ),
            payload={"index": index},
            occurred_at=_NOW,
        )
        for index in range(1, count + 1)
    )


class _Store:
    def __init__(
        self,
        run: AnalysisRun,
        events: tuple[AnalysisRunEvent, ...] = (),
    ) -> None:
        self.run = run
        self.events = events
        self.replay_calls: list[int] = []
        self.run_calls = 0

    async def get_run(self, **_kwargs: object) -> AnalysisRun:
        self.run_calls += 1
        return self.run

    async def list_events(
        self,
        *,
        after_sequence: int,
        limit: int,
        **_kwargs: object,
    ) -> tuple[AnalysisRunEvent, ...]:
        self.replay_calls.append(after_sequence)
        return tuple(
            event
            for event in self.events
            if event.sequence > after_sequence
        )[:limit]


class DurableEventStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_backlog_is_drained_in_order_before_terminal_close(
        self,
    ) -> None:
        run = _run(terminal=True, sequence=5)
        store = _Store(run, _events(run, 5))
        frames = [
            frame
            async for frame in replayable_event_stream(
                store=store,
                user_id=run.user_id,
                run_id=run.run_id,
                config=EventStreamConfig(
                    poll_seconds=0.001,
                    heartbeat_seconds=0.01,
                    batch_size=2,
                ),
            )
        ]
        body = b"".join(frames).decode()

        self.assertEqual(
            [line for line in body.splitlines() if line.startswith("id: ")],
            ["id: 1", "id: 2", "id: 3", "id: 4", "id: 5"],
        )
        self.assertEqual(store.replay_calls, [0, 2, 4])
        self.assertEqual(store.run_calls, 0)

    async def test_idle_stream_sends_heartbeat_and_disconnects_cleanly(
        self,
    ) -> None:
        run = _run(terminal=False, sequence=1)
        store = _Store(run)
        checks = 0

        async def disconnected() -> bool:
            nonlocal checks
            checks += 1
            return checks >= 4

        frames = [
            frame
            async for frame in replayable_event_stream(
                store=store,
                user_id=run.user_id,
                run_id=run.run_id,
                disconnected=disconnected,
                config=EventStreamConfig(
                    poll_seconds=0.001,
                    heartbeat_seconds=0.002,
                    batch_size=10,
                ),
            )
        ]

        self.assertIn(b": heartbeat\n\n", frames)
        self.assertTrue(frames[0].startswith(b"retry: "))
        self.assertEqual(run.status, AnalysisRunStatus.CREATED)
        self.assertEqual(store.run_calls, 1)


if __name__ == "__main__":
    unittest.main()
