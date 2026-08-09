from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apis.analysis_diagnostics import router
from apis.deps import verify_internal_secret
from scripts.data_analysis_agent.runtime.services.diagnostics import (
    AnalysisDiagnosticsSnapshot,
    AnalysisReadiness,
    WorkerDiagnostics,
)


class _Diagnostics:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready

    async def readiness(self) -> AnalysisReadiness:
        return AnalysisReadiness(
            ready=self.ready,
            mongo_ready=self.ready,
            worker=WorkerDiagnostics(
                running=self.ready,
                active_runs=1,
                concurrency=2,
            ),
        )

    async def snapshot(self) -> AnalysisDiagnosticsSnapshot:
        return AnalysisDiagnosticsSnapshot(
            ready=self.ready,
            checked_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
            mongo_ready=self.ready,
            worker=WorkerDiagnostics(
                running=self.ready,
                active_runs=1,
                concurrency=2,
            ),
            queue_depth=3,
            runs_by_status={"created": 3},
            process_metrics={"errors": {}},
        )


def _application(*, authorize: bool, ready: bool = True) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.state.analysis_diagnostics_service = _Diagnostics(ready=ready)
    if authorize:
        application.dependency_overrides[verify_internal_secret] = lambda: None
    return application


class AnalysisDiagnosticsApiTests(unittest.TestCase):
    def test_public_health_and_readiness_do_not_expose_queue_details(self) -> None:
        client = TestClient(_application(authorize=False))

        self.assertEqual(client.get("/health").json(), {"status": "alive"})
        response = client.get("/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {"ready", "mongo_ready", "worker_running"},
        )

    def test_unready_runtime_returns_503(self) -> None:
        client = TestClient(_application(authorize=False, ready=False))

        response = client.get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ready"])

    def test_detailed_diagnostics_require_internal_authentication(self) -> None:
        unauthorized = TestClient(_application(authorize=False))
        authorized = TestClient(_application(authorize=True))

        self.assertIn(
            unauthorized.get("/analysis/diagnostics").status_code,
            {401, 503},
        )
        response = authorized.get("/analysis/diagnostics")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["queue_depth"], 3)


if __name__ == "__main__":
    unittest.main()
