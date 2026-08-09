from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models.runs import AnalysisRunStatus
from ..observability.metrics import AnalysisMetrics, analysis_metrics
from .worker import DurableAnalysisWorker


class WorkerDiagnostics(BaseModel):
    running: bool
    active_runs: int = Field(ge=0)
    concurrency: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisReadiness(BaseModel):
    ready: bool
    mongo_ready: bool
    worker: WorkerDiagnostics

    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisDiagnosticsSnapshot(BaseModel):
    ready: bool
    checked_at: datetime
    mongo_ready: bool
    worker: WorkerDiagnostics
    queue_depth: int = Field(ge=0)
    runs_by_status: dict[str, int] = Field(default_factory=dict)
    process_metrics: dict[str, object] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisDiagnosticsService:
    """Read-only, provider-light operational snapshot for local deployments."""

    def __init__(
        self,
        *,
        database: Any,
        worker: DurableAnalysisWorker,
        metrics: AnalysisMetrics = analysis_metrics,
    ) -> None:
        self._database = database
        self._worker = worker
        self._metrics = metrics

    def _worker_snapshot(self) -> WorkerDiagnostics:
        return WorkerDiagnostics(
            running=self._worker.running,
            active_runs=self._worker.active_run_count,
            concurrency=self._worker.concurrency,
        )

    async def readiness(self) -> AnalysisReadiness:
        """Cheap probe: one ping and no history aggregation."""

        mongo_ready = False
        try:
            await self._database.command("ping")
            mongo_ready = True
        except Exception:
            pass
        worker = self._worker_snapshot()
        return AnalysisReadiness(
            ready=mongo_ready and worker.running,
            mongo_ready=mongo_ready,
            worker=worker,
        )

    async def snapshot(self) -> AnalysisDiagnosticsSnapshot:
        probe = await self.readiness()
        queue_depth = 0
        runs_by_status: dict[str, int] = {}
        if probe.mongo_ready:
            try:
                now = datetime.now(timezone.utc)
                queue_depth = await self._database.analysis_runs.count_documents(
                    {
                        "inputs_ready": True,
                        "cancellation_requested": False,
                        "pause_requested": False,
                        "$or": [
                            {"status": AnalysisRunStatus.CREATED.value},
                            {
                                "status": AnalysisRunStatus.ACTIVE.value,
                                "lease_expires_at": {"$lte": now},
                            },
                        ],
                    }
                )
                grouped = await self._database.analysis_runs.aggregate(
                    [
                        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                        {"$sort": {"_id": 1}},
                    ]
                ).to_list(length=len(AnalysisRunStatus))
                runs_by_status = {
                    str(item.get("_id")): int(item.get("count") or 0)
                    for item in grouped
                    if item.get("_id") is not None
                }
            except Exception:
                # Readiness is determined by ping + worker health. Optional
                # diagnostic aggregation must never make the service unready.
                queue_depth = 0
                runs_by_status = {}
        return AnalysisDiagnosticsSnapshot(
            ready=probe.ready,
            checked_at=datetime.now(timezone.utc),
            mongo_ready=probe.mongo_ready,
            worker=probe.worker,
            queue_depth=max(0, int(queue_depth)),
            runs_by_status=runs_by_status,
            process_metrics=self._metrics.snapshot(),
        )


__all__ = [
    "AnalysisDiagnosticsService",
    "AnalysisDiagnosticsSnapshot",
    "AnalysisReadiness",
    "WorkerDiagnostics",
]
