from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apis.analysis_runs import router
from apis.deps import current_user_id, verify_internal_secret
from scripts.data_analysis_agent.runtime.models.events import (
    AnalysisEventType,
    AnalysisRunEvent,
)
from scripts.data_analysis_agent.runtime.models.runs import (
    AnalysisMode,
    AnalysisRun,
    AnalysisRunOutcome,
    AnalysisRunPhase,
    AnalysisRunStatus,
)
from scripts.data_analysis_agent.runtime.repositories.runs import (
    CreateRunResult,
    RunMutationResult,
)
from scripts.data_analysis_agent.runtime.services.run_service import (
    AnalysisRunPage,
)
from scripts.data_analysis_agent.runtime.services.sse_connections import (
    SSEConnectionLimitError,
    SSEConnectionLimiter,
)


_NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


def _run(*, terminal: bool = False) -> AnalysisRun:
    values: dict[str, object] = {
        "run_id": str(uuid4()),
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "chat_id": "chat-1",
        "idempotency_key": "idempotency-key-1",
        "request_fingerprint": "a" * 64,
        "mode": AnalysisMode.ANALYSE,
        "prompt": "Profile the selected data.",
        "version": 1,
        "last_event_sequence": 1,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    if terminal:
        values.update(
            {
                "status": AnalysisRunStatus.SUCCEEDED,
                "phase": AnalysisRunPhase.COMPLETED,
                "outcome": AnalysisRunOutcome.DATASETS_PREPARED,
                "version": 2,
                "last_event_sequence": 2,
                "started_at": _NOW,
                "completed_at": _NOW,
            }
        )
    return AnalysisRun.model_validate(values)


def _event(
    run: AnalysisRun,
    *,
    sequence: int,
    event_type: AnalysisEventType,
) -> AnalysisRunEvent:
    return AnalysisRunEvent(
        run_id=run.run_id,
        user_id=run.user_id,
        workspace_id=run.workspace_id,
        sequence=sequence,
        event_type=event_type,
        status=(
            AnalysisRunStatus.CREATED
            if sequence == 1
            else AnalysisRunStatus.SUCCEEDED
        ),
        phase=(
            AnalysisRunPhase.CONTEXT_RESOLUTION
            if sequence == 1
            else AnalysisRunPhase.COMPLETED
        ),
        payload={"dataset_count": sequence - 1},
        occurred_at=_NOW,
    )


class _FakeRunService:
    def __init__(self, *, terminal: bool = False) -> None:
        self.run = _run(terminal=terminal)
        self.events = (
            _event(
                self.run,
                sequence=1,
                event_type=AnalysisEventType.RUN_CREATED,
            ),
            *(
                (
                    _event(
                        self.run,
                        sequence=2,
                        event_type=AnalysisEventType.RUN_COMPLETED,
                    ),
                )
                if terminal
                else ()
            ),
        )
        self.calls: list[tuple[str, object]] = []
        self.created = True
        self.allow_read = True

    @property
    def event_store(self) -> "_FakeRunService":
        return self

    async def create_run(self, **kwargs: object) -> CreateRunResult:
        self.calls.append(("create", kwargs))
        return CreateRunResult(
            run=self.run,
            event=self.events[0],
            created=self.created,
        )

    async def get_run(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> AnalysisRun | None:
        self.calls.append(("get", (user_id, run_id)))
        if (
            self.allow_read
            and user_id == self.run.user_id
            and run_id == self.run.run_id
        ):
            return self.run
        return None

    async def list_runs(self, **kwargs: object) -> AnalysisRunPage:
        self.calls.append(("list", kwargs))
        return AnalysisRunPage(items=(self.run,), next_cursor="opaque-next")

    async def cancel_run(self, **kwargs: object) -> RunMutationResult:
        self.calls.append(("cancel", kwargs))
        return RunMutationResult(run=self.run, event=None, changed=False)

    async def list_events(
        self,
        *,
        user_id: str,
        run_id: str,
        after_sequence: int,
        limit: int,
    ) -> tuple[AnalysisRunEvent, ...]:
        self.calls.append(
            ("events", (user_id, run_id, after_sequence, limit))
        )
        return tuple(
            event
            for event in self.events
            if event.sequence > after_sequence
        )[:limit]


class _RejectingLimiter:
    async def acquire(self, **_kwargs: object) -> object:
        raise SSEConnectionLimitError("event-stream capacity is exhausted")


def _client(
    service: _FakeRunService,
    *,
    limiter: object | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.analysis_run_service = service
    app.state.analysis_sse_limiter = limiter or SSEConnectionLimiter()
    app.dependency_overrides[current_user_id] = lambda: "user-1"
    app.dependency_overrides[verify_internal_secret] = lambda: None
    return TestClient(app)


class AnalysisRunAPITests(unittest.TestCase):
    def test_create_requires_idempotency_key(self) -> None:
        service = _FakeRunService()
        with _client(service) as client:
            response = client.post(
                "/analysis/runs",
                json={
                    "workspace_id": "workspace-1",
                    "mode": "analyse",
                    "prompt": "Profile it",
                    "selected_document_ids": ["b" * 64],
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(service.calls)

    def test_create_is_accepted_and_hides_control_plane_secrets(self) -> None:
        service = _FakeRunService()
        with _client(service) as client:
            response = client.post(
                "/analysis/runs",
                headers={
                    "Idempotency-Key": "client-request-123",
                    "X-Request-ID": "request-123",
                },
                json={
                    "workspace_id": "workspace-1",
                    "mode": "analyse",
                    "prompt": "Profile it",
                    "selected_document_ids": ["b" * 64],
                },
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.headers["location"],
            f"/analysis/runs/{service.run.run_id}",
        )
        payload = response.json()
        self.assertTrue(payload["created"])
        self.assertEqual(payload["run"]["prompt"], service.run.prompt)
        self.assertTrue(payload["run"]["inputs_ready"])
        self.assertNotIn("user_id", payload["run"])
        self.assertNotIn("worker_id", payload["run"])
        self.assertNotIn("idempotency_key", payload["run"])
        call = service.calls[0][1]
        assert isinstance(call, dict)
        self.assertEqual(call["user_id"], "user-1")
        self.assertEqual(call["idempotency_key"], "client-request-123")

    def test_list_uses_opaque_cursor_and_status_filter(self) -> None:
        service = _FakeRunService()
        with _client(service) as client:
            response = client.get(
                "/analysis/runs",
                params={
                    "workspace_id": "workspace-1",
                    "status": "created",
                    "cursor": "opaque.input",
                    "limit": 10,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["next_cursor"], "opaque-next")
        call = service.calls[0][1]
        assert isinstance(call, dict)
        self.assertEqual(call["cursor"], "opaque.input")
        self.assertEqual(call["status"], AnalysisRunStatus.CREATED)

    def test_cancel_forwards_optimistic_version(self) -> None:
        service = _FakeRunService()
        with _client(service) as client:
            response = client.post(
                f"/analysis/runs/{service.run.run_id}/cancel",
                json={"expected_version": 7},
            )

        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.json()["changed"])
        call = service.calls[0][1]
        assert isinstance(call, dict)
        self.assertEqual(call["expected_version"], 7)

    def test_sse_replays_after_last_event_id_with_stable_transport_name(self) -> None:
        service = _FakeRunService(terminal=True)
        with _client(service) as client:
            response = client.get(
                f"/analysis/runs/{service.run.run_id}/events?after=0",
                headers={"Last-Event-ID": "1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/event-stream; charset=utf-8")
        self.assertIn("retry: 2000", response.text)
        self.assertIn("id: 2", response.text)
        self.assertNotIn("id: 1\n", response.text)
        self.assertIn("event: analysis_run_event", response.text)
        self.assertIn('"event_type":"run_completed"', response.text)
        self.assertNotIn(service.run.prompt, response.text)
        event_calls = [call for call in service.calls if call[0] == "events"]
        self.assertEqual(event_calls[0][1][2], 1)

    def test_sse_authorizes_before_reading_events(self) -> None:
        service = _FakeRunService(terminal=True)
        service.allow_read = False
        with _client(service) as client:
            response = client.get(
                f"/analysis/runs/{service.run.run_id}/events",
            )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(any(call[0] == "events" for call in service.calls))

    def test_sse_rejects_non_numeric_last_event_id(self) -> None:
        service = _FakeRunService(terminal=True)
        with _client(service) as client:
            response = client.get(
                f"/analysis/runs/{service.run.run_id}/events",
                headers={"Last-Event-ID": "event-two"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(any(call[0] == "events" for call in service.calls))

    def test_sse_rejects_cursor_ahead_of_durable_stream(self) -> None:
        service = _FakeRunService(terminal=True)
        with _client(service) as client:
            response = client.get(
                f"/analysis/runs/{service.run.run_id}/events",
                headers={"Last-Event-ID": "99"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"],
            "event_cursor_ahead",
        )
        self.assertEqual(
            response.json()["detail"]["last_event_sequence"],
            service.run.last_event_sequence,
        )
        self.assertFalse(any(call[0] == "events" for call in service.calls))

    def test_sse_rejects_connections_over_capacity(self) -> None:
        service = _FakeRunService(terminal=True)
        with _client(service, limiter=_RejectingLimiter()) as client:
            response = client.get(
                f"/analysis/runs/{service.run.run_id}/events",
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "2")
        self.assertFalse(any(call[0] == "events" for call in service.calls))


if __name__ == "__main__":
    unittest.main()
