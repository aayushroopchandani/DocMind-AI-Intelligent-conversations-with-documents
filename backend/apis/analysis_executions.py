"""Tenant-scoped reads of what a run actually executed (Phase 9.14.1).

    GET .../execution           what ran, how much it moved, and how it ended
    GET .../execution/preview   the bounded, already-redacted result sample

The split is deliberate. The first route answers from MongoDB alone, so a client
watching a run can poll it without touching blob storage. Only the second spends
a download, and only on a result that has actually published.

Nothing here exposes how an execution is addressed internally. The execution key
is a deterministic content hash used for idempotency and cache lookup; the
recipe hash and input signatures are what it is derived from. A client addresses
a run, so those stay server-side — the same boundary `AnalysisRunView` already
draws when it withholds `current_execution_id` from the run view.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from apis.analysis_runs import (
    AnalysisRunAPIService,
    AnalysisRunView,
    get_analysis_run_service,
)
from apis.deps import current_user_id, verify_internal_secret
from scripts.data_analysis_agent.runtime.execution.results.previews import (
    ResultPreview,
)
from scripts.data_analysis_agent.runtime.execution.results.reader import (
    ResultUnavailableError,
)
from scripts.data_analysis_agent.runtime.models.executions import (
    AnalysisExecution,
    ExecutionMetrics,
    ExecutionStatus,
    StageStatus,
)
from scripts.data_analysis_agent.runtime.models.plans import PlanColumn
from scripts.data_analysis_agent.runtime.repositories.executions import (
    ExecutionNotFoundError,
    ExecutionRepositoryError,
)
from scripts.data_analysis_agent.runtime.repositories.runs import (
    AnalysisRunNotFoundError,
    AnalysisRunStoreError,
)
from scripts.data_analysis_agent.runtime.services.execution_reader import (
    ExecutionReadService,
)


router = APIRouter(prefix="/analysis/runs", tags=["analysis-executions"])

TraceId = Annotated[
    str | None,
    Header(
        alias="X-Request-ID",
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$",
    ),
]


class AnalysisExecutionAPIService(Protocol):
    """Composition boundary implemented by the durable read service."""

    async def get_for_run(
        self,
        *,
        user_id: str,
        run_id: str,
        execution_id: str | None = None,
    ) -> AnalysisExecution: ...

    async def read_preview(
        self,
        *,
        user_id: str,
        run_id: str,
        execution_id: str | None = None,
    ) -> tuple[AnalysisExecution, ResultPreview]: ...


def get_analysis_execution_service(request: Request) -> AnalysisExecutionAPIService:
    service = getattr(request.app.state, "analysis_execution_reader", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis execution history is unavailable",
        )
    return cast(ExecutionReadService, service)


class ExecutionStageView(BaseModel):
    """One logical stage, without the checkpoint that lets a worker resume it."""

    stage_id: str
    step_ids: tuple[str, ...]
    status: StageStatus
    input_rows: int
    output_rows: int
    output_columns: int
    duration_ms: float

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecutionView(BaseModel):
    """Tenant-safe execution record; idempotency internals stay private."""

    execution_id: str
    run_id: str
    plan_id: str
    plan_hash: str
    status: ExecutionStatus
    engine_version: str
    semantics_version: str
    current_stage_id: str | None
    stages: tuple[ExecutionStageView, ...]
    #: True once a result bundle is published and readable.
    has_result: bool
    result_content_hash: str | None
    result_columns: tuple[PlanColumn, ...]
    metrics: ExecutionMetrics
    failure_code: str | None
    failure_message: str | None
    warnings: tuple[str, ...]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime

    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def from_execution(cls, execution: AnalysisExecution) -> "ExecutionView":
        return cls(
            execution_id=execution.execution_id,
            run_id=execution.run_id,
            plan_id=execution.plan_id,
            plan_hash=execution.plan_hash,
            status=execution.status,
            engine_version=execution.engine_version,
            semantics_version=execution.semantics_version,
            current_stage_id=execution.current_stage_id,
            stages=tuple(
                ExecutionStageView(
                    stage_id=stage.stage_id,
                    step_ids=stage.step_ids,
                    status=stage.status,
                    input_rows=stage.input_rows,
                    output_rows=stage.output_rows,
                    output_columns=stage.output_columns,
                    duration_ms=stage.duration_ms,
                )
                for stage in execution.stages
            ),
            has_result=execution.artifacts is not None,
            result_content_hash=execution.result_content_hash,
            result_columns=execution.result_columns,
            metrics=execution.metrics,
            failure_code=execution.failure_code,
            failure_message=execution.failure_message,
            warnings=execution.warnings,
            created_at=execution.created_at,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            updated_at=execution.updated_at,
        )


class ExecutionResponse(BaseModel):
    execution: ExecutionView
    run: AnalysisRunView

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecutionPreviewResponse(BaseModel):
    """The sample, bound to the exact result it was taken from."""

    execution_id: str
    #: The result's content hash, so a client can tell which result this
    #: samples — two runs of the same recipe produce the same digest.
    content_hash: str | None
    preview: ResultPreview

    model_config = ConfigDict(extra="forbid", frozen=True)


def _execution_error(error: Exception) -> None:
    if isinstance(error, (ExecutionNotFoundError, AnalysisRunNotFoundError)):
        raise HTTPException(
            status_code=404,
            detail="Analysis execution not found",
        ) from error
    if isinstance(error, ResultUnavailableError):
        # The execution is real and readable; it just has no published result
        # to sample yet. That is a state conflict, not a missing resource.
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, (ExecutionRepositoryError, AnalysisRunStoreError)):
        raise HTTPException(
            status_code=503,
            detail="Analysis execution history is temporarily unavailable",
        ) from error
    raise error


async def _authorized_run(
    *,
    run_id: UUID,
    user_id: str,
    runs: AnalysisRunAPIService,
):
    """Resolve the run first, so a cross-tenant read is a 404 either way."""

    run = await runs.get_run(user_id=user_id, run_id=str(run_id))
    if run is None:
        raise AnalysisRunNotFoundError("analysis run not found")
    return run


@router.get("/{run_id}/execution", response_model=ExecutionResponse)
async def get_run_execution(
    run_id: UUID,
    response: Response,
    trace_id: TraceId = None,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    runs: AnalysisRunAPIService = Depends(get_analysis_run_service),
    executions: AnalysisExecutionAPIService = Depends(
        get_analysis_execution_service
    ),
) -> ExecutionResponse:
    """Return what this run executed, without touching blob storage."""

    response.headers["Cache-Control"] = "no-store"
    if trace_id:
        response.headers["X-Request-ID"] = trace_id
    try:
        run = await _authorized_run(run_id=run_id, user_id=user_id, runs=runs)
        execution = await executions.get_for_run(
            user_id=user_id,
            run_id=str(run_id),
            execution_id=run.current_execution_id,
        )
    except Exception as error:  # narrowed by _execution_error
        _execution_error(error)
        raise  # pragma: no cover - _execution_error always raises
    return ExecutionResponse(
        execution=ExecutionView.from_execution(execution),
        run=AnalysisRunView.from_run(run),
    )


@router.get(
    "/{run_id}/execution/preview",
    response_model=ExecutionPreviewResponse,
)
async def get_run_execution_preview(
    run_id: UUID,
    response: Response,
    trace_id: TraceId = None,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    runs: AnalysisRunAPIService = Depends(get_analysis_run_service),
    executions: AnalysisExecutionAPIService = Depends(
        get_analysis_execution_service
    ),
) -> ExecutionPreviewResponse:
    """Return the bounded, redacted sample this run's result published."""

    response.headers["Cache-Control"] = "no-store"
    if trace_id:
        response.headers["X-Request-ID"] = trace_id
    try:
        run = await _authorized_run(run_id=run_id, user_id=user_id, runs=runs)
        execution, preview = await executions.read_preview(
            user_id=user_id,
            run_id=str(run_id),
            execution_id=run.current_execution_id,
        )
    except Exception as error:  # narrowed by _execution_error
        _execution_error(error)
        raise  # pragma: no cover - _execution_error always raises
    return ExecutionPreviewResponse(
        execution_id=execution.execution_id,
        content_hash=execution.result_content_hash,
        preview=preview,
    )


__all__ = [
    "AnalysisExecutionAPIService",
    "ExecutionPreviewResponse",
    "ExecutionResponse",
    "ExecutionStageView",
    "ExecutionView",
    "get_analysis_execution_service",
    "router",
]
