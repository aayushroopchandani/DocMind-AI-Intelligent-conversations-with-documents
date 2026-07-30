"""Public control-plane API for durable data-analysis runs.

This router deliberately exposes only run creation, observation, cancellation,
and durable event replay. Typed plans and approval/apply endpoints belong to a
later phase and must not be inferred from the current prepared-dataset outcome.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Protocol, cast
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from apis.deps import current_user_id, verify_internal_secret
from config.settings import settings
from scripts.data_analysis_agent.runtime.models.requests import (
    CreateAnalysisRunRequest,
)
from scripts.data_analysis_agent.runtime.models.runs import (
    AnalysisMode,
    AnalysisRun,
    AnalysisRunOutcome,
    AnalysisRunPhase,
    AnalysisRunStatus,
    DatasetVersionReference,
    RunIssueSummary,
    TokenUsage,
)
from scripts.data_analysis_agent.runtime.repositories.runs import (
    AnalysisRunConflictError,
    AnalysisRunIdempotencyConflictError,
    AnalysisRunNotFoundError,
    AnalysisRunStoreError,
    CreateRunResult,
    RunMutationResult,
)
from scripts.data_analysis_agent.runtime.repositories.artifacts import (
    ArtifactRepositoryError,
    ArtifactStateConflictError,
)
from scripts.data_analysis_agent.runtime.repositories.datasets import (
    DatasetCatalogConflictError,
    DatasetCatalogError,
)
from scripts.data_analysis_agent.runtime.services.artifacts import (
    ArtifactServiceError,
    ArtifactVersionInProgressError,
)
from scripts.data_analysis_agent.runtime.services.event_stream import (
    EventReplayStore,
    EventStreamConfig,
    replayable_event_stream,
)
from scripts.data_analysis_agent.runtime.services.run_service import (
    AnalysisRunPage,
    AnalysisRunServiceError,
    InvalidRunCursorError,
)
from scripts.data_analysis_agent.runtime.services.state_machine import (
    InvalidAnalysisRunTransition,
)
from scripts.data_analysis_agent.runtime.services.sse_connections import (
    SSEConnectionLease,
    SSEConnectionLimitError,
    SSEConnectionLimiter,
)
from scripts.data_analysis_agent.runtime.services.workbook_context import (
    WorkbookContextError,
    WorkbookContextTooLargeError,
)
from scripts.data_analysis_agent.runtime.storage.validation import (
    ArtifactValidationError,
)


router = APIRouter(prefix="/analysis/runs", tags=["analysis-runs"])

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
        pattern=r"^[!-~]{8,200}$",
        description="Stable key for retrying the same create request.",
    ),
]
TraceId = Annotated[
    str | None,
    Header(
        alias="X-Request-ID",
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$",
    ),
]


class AnalysisRunAPIService(Protocol):
    """Composition boundary implemented by the durable runtime service."""

    @property
    def event_store(self) -> EventReplayStore: ...

    async def create_run(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        request: CreateAnalysisRunRequest,
        trace_id: str | None = None,
    ) -> CreateRunResult: ...

    async def get_run(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> AnalysisRun | None: ...

    async def list_runs(
        self,
        *,
        user_id: str,
        workspace_id: str | None,
        status: AnalysisRunStatus | None,
        cursor: str | None,
        limit: int,
    ) -> AnalysisRunPage: ...

    async def cancel_run(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int | None = None,
        trace_id: str | None = None,
    ) -> RunMutationResult: ...


def get_analysis_run_service(request: Request) -> AnalysisRunAPIService:
    """Resolve the process-wide runtime assembled during application startup."""

    service = getattr(request.app.state, "analysis_run_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis runtime is unavailable",
        )
    return cast(AnalysisRunAPIService, service)


def get_analysis_sse_limiter(request: Request) -> SSEConnectionLimiter:
    limiter = getattr(request.app.state, "analysis_sse_limiter", None)
    if limiter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis event streaming is unavailable",
        )
    return cast(SSEConnectionLimiter, limiter)


async def _admitted_event_stream(
    *,
    stream: AsyncIterator[bytes],
    limiter: SSEConnectionLimiter,
    lease: SSEConnectionLease,
) -> AsyncIterator[bytes]:
    try:
        async for frame in stream:
            yield frame
    finally:
        await limiter.release(lease)


class AnalysisRunView(BaseModel):
    """Tenant-safe run representation; operational lease internals stay private."""

    schema_version: int
    run_id: str
    workspace_id: str
    chat_id: str
    mode: AnalysisMode
    prompt: str
    active_artifact_id: str | None
    status: AnalysisRunStatus
    phase: AnalysisRunPhase
    outcome: AnalysisRunOutcome | None
    inputs_ready: bool
    cancellation_requested: bool
    version: int
    last_event_sequence: int
    input_artifact_version_ids: tuple[str, ...]
    input_dataset_versions: tuple[DatasetVersionReference, ...]
    selected_document_ids: tuple[str, ...]
    final_artifact_ids: tuple[str, ...]
    final_dataset_ids: tuple[str, ...]
    warnings_summary: tuple[RunIssueSummary, ...]
    errors_summary: tuple[RunIssueSummary, ...]
    model_versions: dict[str, str]
    prompt_versions: dict[str, str]
    token_usage: TokenUsage
    timings_ms: dict[str, float]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime | None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def from_run(cls, run: AnalysisRun) -> "AnalysisRunView":
        return cls.model_validate(
            run.model_dump(
                exclude={
                    "user_id",
                    "idempotency_key",
                    "request_fingerprint",
                    "worker_id",
                    "lease_expires_at",
                    "lease_attempt",
                    "cancellation_requested_at",
                }
            )
        )


class CreateAnalysisRunResponse(BaseModel):
    created: bool
    run: AnalysisRunView

    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisRunListResponse(BaseModel):
    items: tuple[AnalysisRunView, ...]
    next_cursor: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class CancelAnalysisRunRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class CancelAnalysisRunResponse(BaseModel):
    changed: bool
    run: AnalysisRunView

    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical_run_id(run_id: UUID) -> str:
    return str(run_id)


def _raise_public_error(exc: Exception) -> None:
    if isinstance(exc, AnalysisRunNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis run not found",
        ) from exc
    if isinstance(exc, AnalysisRunIdempotencyConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used for a different request",
        ) from exc
    if isinstance(
        exc,
        (
            AnalysisRunConflictError,
            InvalidAnalysisRunTransition,
            ArtifactVersionInProgressError,
            ArtifactStateConflictError,
            DatasetCatalogConflictError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if isinstance(exc, WorkbookContextTooLargeError):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    if isinstance(exc, (WorkbookContextError, ArtifactValidationError)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if isinstance(exc, InvalidRunCursorError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid analysis run cursor",
        ) from exc
    if isinstance(
        exc,
        (
            AnalysisRunStoreError,
            AnalysisRunServiceError,
            ArtifactServiceError,
            ArtifactRepositoryError,
            DatasetCatalogError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis runtime is temporarily unavailable",
        ) from exc
    raise exc


@router.post(
    "",
    response_model=CreateAnalysisRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_analysis_run(
    body: CreateAnalysisRunRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    trace_id: TraceId = None,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    service: AnalysisRunAPIService = Depends(get_analysis_run_service),
) -> CreateAnalysisRunResponse:
    try:
        result = await service.create_run(
            user_id=user_id,
            idempotency_key=idempotency_key,
            request=body,
            trace_id=trace_id,
        )
    except Exception as exc:
        _raise_public_error(exc)
        raise  # pragma: no cover - _raise_public_error always raises

    response.headers["Location"] = f"/analysis/runs/{result.run.run_id}"
    response.headers["Cache-Control"] = "no-store"
    if not result.created:
        response.headers["Idempotent-Replay"] = "true"
    return CreateAnalysisRunResponse(
        created=result.created,
        run=AnalysisRunView.from_run(result.run),
    )


@router.get("", response_model=AnalysisRunListResponse)
async def list_analysis_runs(
    response: Response,
    workspace_id: str | None = Query(default=None, min_length=1, max_length=200),
    run_status: AnalysisRunStatus | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    limit: int = Query(default=25, ge=1, le=100),
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    service: AnalysisRunAPIService = Depends(get_analysis_run_service),
) -> AnalysisRunListResponse:
    try:
        page = await service.list_runs(
            user_id=user_id,
            workspace_id=workspace_id,
            status=run_status,
            cursor=cursor,
            limit=limit,
        )
    except Exception as exc:
        _raise_public_error(exc)
        raise  # pragma: no cover
    response.headers["Cache-Control"] = "no-store"
    return AnalysisRunListResponse(
        items=tuple(AnalysisRunView.from_run(run) for run in page.items),
        next_cursor=page.next_cursor,
    )


@router.get("/{run_id}", response_model=AnalysisRunView)
async def get_analysis_run(
    run_id: UUID,
    response: Response,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    service: AnalysisRunAPIService = Depends(get_analysis_run_service),
) -> AnalysisRunView:
    try:
        run = await service.get_run(
            user_id=user_id,
            run_id=_canonical_run_id(run_id),
        )
    except Exception as exc:
        _raise_public_error(exc)
        raise  # pragma: no cover
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis run not found",
        )
    response.headers["Cache-Control"] = "no-store"
    return AnalysisRunView.from_run(run)


@router.post(
    "/{run_id}/cancel",
    response_model=CancelAnalysisRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_analysis_run(
    run_id: UUID,
    body: CancelAnalysisRunRequest,
    response: Response,
    trace_id: TraceId = None,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    service: AnalysisRunAPIService = Depends(get_analysis_run_service),
) -> CancelAnalysisRunResponse:
    try:
        result = await service.cancel_run(
            user_id=user_id,
            run_id=_canonical_run_id(run_id),
            expected_version=body.expected_version,
            trace_id=trace_id,
        )
    except Exception as exc:
        _raise_public_error(exc)
        raise  # pragma: no cover
    response.headers["Cache-Control"] = "no-store"
    return CancelAnalysisRunResponse(
        changed=result.changed,
        run=AnalysisRunView.from_run(result.run),
    )


def _resume_sequence(
    *,
    last_event_id: str | None,
    after_sequence: int,
) -> int:
    if last_event_id is None or not last_event_id.strip():
        return after_sequence
    raw = last_event_id.strip()
    if not raw.isascii() or not raw.isdecimal():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Last-Event-ID must be a non-negative event sequence",
        )
    return int(raw)


@router.get(
    "/{run_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Replayable durable analysis-run events.",
        }
    },
)
async def stream_analysis_run_events(
    run_id: UUID,
    request: Request,
    after_sequence: int = Query(default=0, alias="after", ge=0),
    last_event_id: str | None = Header(
        default=None,
        alias="Last-Event-ID",
        max_length=100,
    ),
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    service: AnalysisRunAPIService = Depends(get_analysis_run_service),
    limiter: SSEConnectionLimiter = Depends(get_analysis_sse_limiter),
) -> StreamingResponse:
    canonical_run_id = _canonical_run_id(run_id)
    try:
        run = await service.get_run(
            user_id=user_id,
            run_id=canonical_run_id,
        )
    except Exception as exc:
        _raise_public_error(exc)
        raise  # pragma: no cover
    if run is None:
        # Authorize before constructing the response. A missing run and a run
        # owned by another tenant intentionally have the same public result.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis run not found",
        )

    cursor = _resume_sequence(
        last_event_id=last_event_id,
        after_sequence=after_sequence,
    )
    if cursor > run.last_event_sequence:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "event_cursor_ahead",
                "message": "Event cursor is ahead of the durable stream.",
                "last_event_sequence": run.last_event_sequence,
            },
        )
    try:
        connection_lease = await limiter.acquire(
            user_id=user_id,
            run_id=canonical_run_id,
        )
    except SSEConnectionLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": "2"},
        ) from exc
    stream = replayable_event_stream(
        store=service.event_store,
        user_id=user_id,
        run_id=canonical_run_id,
        after_sequence=cursor,
        disconnected=request.is_disconnected,
        config=EventStreamConfig(
            poll_seconds=settings.analysis_sse_poll_seconds,
            heartbeat_seconds=settings.analysis_sse_heartbeat_seconds,
            batch_size=settings.analysis_sse_batch_size,
        ),
    )
    return StreamingResponse(
        _admitted_event_stream(
            stream=stream,
            limiter=limiter,
            lease=connection_lease,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = [
    "AnalysisRunAPIService",
    "AnalysisRunListResponse",
    "AnalysisRunPage",
    "AnalysisRunView",
    "CancelAnalysisRunRequest",
    "CancelAnalysisRunResponse",
    "CreateAnalysisRunResponse",
    "get_analysis_sse_limiter",
    "get_analysis_run_service",
    "router",
]
