"""Liveness, readiness, and internal Phase-8 operational diagnostics."""

from __future__ import annotations

from typing import Protocol, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from apis.deps import verify_internal_secret
from scripts.data_analysis_agent.runtime.services.diagnostics import (
    AnalysisDiagnosticsSnapshot,
    AnalysisReadiness,
)


router = APIRouter(tags=["diagnostics"])


class DiagnosticsProvider(Protocol):
    async def readiness(self) -> AnalysisReadiness: ...

    async def snapshot(self) -> AnalysisDiagnosticsSnapshot: ...


class LivenessResponse(BaseModel):
    status: str = "alive"

    model_config = ConfigDict(extra="forbid", frozen=True)


class ReadinessResponse(BaseModel):
    ready: bool
    mongo_ready: bool
    worker_running: bool

    model_config = ConfigDict(extra="forbid", frozen=True)


def get_diagnostics_service(request: Request) -> DiagnosticsProvider:
    service = getattr(request.app.state, "analysis_diagnostics_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis runtime is unavailable",
        )
    return cast(DiagnosticsProvider, service)


@router.get("/health", response_model=LivenessResponse)
async def liveness(response: Response) -> LivenessResponse:
    response.headers["Cache-Control"] = "no-store"
    return LivenessResponse()


@router.get("/ready", response_model=ReadinessResponse)
async def readiness(
    response: Response,
    service: DiagnosticsProvider = Depends(get_diagnostics_service),
) -> ReadinessResponse:
    snapshot = await service.readiness()
    response.headers["Cache-Control"] = "no-store"
    if not snapshot.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        ready=snapshot.ready,
        mongo_ready=snapshot.mongo_ready,
        worker_running=snapshot.worker.running,
    )


@router.get(
    "/analysis/diagnostics",
    response_model=AnalysisDiagnosticsSnapshot,
    dependencies=[Depends(verify_internal_secret)],
)
async def analysis_diagnostics(
    response: Response,
    service: DiagnosticsProvider = Depends(get_diagnostics_service),
) -> AnalysisDiagnosticsSnapshot:
    response.headers["Cache-Control"] = "no-store"
    return await service.snapshot()


__all__ = ["get_diagnostics_service", "router"]
