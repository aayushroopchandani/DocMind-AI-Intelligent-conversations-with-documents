"""Tenant-scoped endpoints for the workbook patch lifecycle (Phase 9.11–9.12).

The whole flow the browser drives:

    POST .../patch/context     the live workbook, hashed  -> a compiled patch
    GET  .../patch             what is waiting for approval
    POST .../patch/approve     the final, binding decision
    GET  .../patch/.../chunks  payload blocks, one at a time
    POST .../patch/preflight   the last check before mutating anything
    POST .../patch/receipt     what actually happened
    POST .../patch/undo        the stored inverse, as a new action

Every one is idempotent and every one is bound to hashes the server computed.
Nothing here trusts a client claim it cannot check.
"""

from __future__ import annotations

from typing import Annotated, Literal, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from apis.analysis_runs import (
    AnalysisRunAPIService,
    AnalysisRunView,
    get_analysis_run_service,
)
from apis.deps import current_user_id, verify_internal_secret
from scripts.data_analysis_agent.runtime.models.patches import (
    PatchApprovalCommand,
    PatchBinding,
    PatchProposal,
    PatchRejectionReason,
)
from scripts.data_analysis_agent.runtime.patches.receipt import (
    PatchApplicationReceipt,
)
from scripts.data_analysis_agent.runtime.placement import (
    CapturedRange,
    WorkbookPatchContext,
)
from scripts.data_analysis_agent.runtime.repositories.patches import (
    PatchConflictError,
    PatchRepositoryError,
)
from scripts.data_analysis_agent.runtime.repositories.reservations import (
    SpatialReservationError,
)
from scripts.data_analysis_agent.runtime.repositories.runs import (
    AnalysisRunNotFoundError,
    AnalysisRunStoreError,
)
from scripts.data_analysis_agent.runtime.services.patch_service import (
    PatchNotReadyError,
    PatchService,
    PatchServiceError,
    PreflightResult,
)


router = APIRouter(prefix="/analysis/runs", tags=["analysis-patches"])

TraceId = Annotated[
    str | None,
    Header(
        alias="X-Request-ID",
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$",
    ),
]

MAX_LIVE_CAPTURES = 32


class AnalysisPatchAPIService(Protocol):
    async def submit_context(self, **kwargs: object) -> PatchProposal: ...

    async def decide(self, **kwargs: object) -> PatchProposal: ...

    async def preflight(self, **kwargs: object) -> PreflightResult: ...

    async def record_application(self, **kwargs: object) -> PatchProposal: ...

    async def propose_undo(self, **kwargs: object) -> PatchProposal: ...

    async def read_payload_chunk(self, **kwargs: object) -> bytes: ...


def get_analysis_patch_service(request: Request) -> AnalysisPatchAPIService:
    service = getattr(request.app.state, "analysis_patch_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workbook patching is unavailable",
        )
    return cast(PatchService, service)


def get_patch_repository(request: Request):
    repository = getattr(request.app.state, "analysis_patch_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workbook patching is unavailable",
        )
    return repository


class PatchContextRequest(BaseModel):
    """The captured live workbook, posted once output size is known."""

    context: WorkbookPatchContext
    sheet_name_hint: str | None = Field(default=None, max_length=255)

    model_config = ConfigDict(extra="forbid", frozen=True)


class PatchDecisionRequest(BaseModel):
    """Final HITL, bound to the exact patch the reviewer saw (9.12.1)."""

    approval_type: Literal["patch"] = "patch"
    patch_id: str = Field(min_length=1, max_length=120)
    patch_revision: int = Field(ge=1)
    patch_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_workbook_revision: int = Field(ge=0)
    decision_id: str = Field(min_length=8, max_length=200)
    comment: str | None = Field(default=None, max_length=1_000)
    reason: PatchRejectionReason | None = None

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    def command(
        self,
        decision: Literal["approve", "reject"],
    ) -> PatchApprovalCommand:
        return PatchApprovalCommand(
            decision=decision,
            binding=PatchBinding(
                patch_id=self.patch_id,
                patch_revision=self.patch_revision,
                patch_hash=self.patch_hash,
                plan_hash=self.plan_hash,
                base_workbook_revision=self.base_workbook_revision,
            ),
            decision_id=self.decision_id,
            comment=self.comment,
            rejection_reason=self.reason if decision == "reject" else None,
        )


class PatchPreflightRequest(BaseModel):
    """What the client reads immediately before touching a cell (9.12.3)."""

    patch_id: str = Field(min_length=1, max_length=120)
    patch_revision: int = Field(ge=1)
    workbook_revision: int = Field(ge=0)
    workbook_present: bool = True
    captures: tuple[CapturedRange, ...] = Field(
        default=(),
        max_length=MAX_LIVE_CAPTURES,
    )

    model_config = ConfigDict(extra="forbid", frozen=True)

    def live(self):
        return {
            (item.worksheet_id, item.range_a1): item.cells
            for item in self.captures
        }


class PatchUndoRequest(BaseModel):
    patch_id: str = Field(min_length=1, max_length=120)
    patch_revision: int = Field(ge=1)
    workbook_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=200)

    model_config = ConfigDict(extra="forbid", frozen=True)


class PatchProposalResponse(BaseModel):
    proposal: PatchProposal
    run: AnalysisRunView

    model_config = ConfigDict(extra="forbid", frozen=True)


class PatchPreflightResponse(BaseModel):
    may_apply: bool
    conflict: str
    resolution: str
    message: str
    issue_codes: tuple[str, ...] = ()
    proposal: PatchProposal
    rebased: PatchProposal | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


def _patch_error(error: Exception) -> None:
    if isinstance(error, PatchNotReadyError):
        code = 404 if error.code in _NOT_FOUND_CODES else 409
        raise HTTPException(status_code=code, detail=error.message) from error
    if isinstance(error, (PatchConflictError, PatchServiceError, ValueError)):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, AnalysisRunNotFoundError):
        raise HTTPException(status_code=404, detail="Analysis run not found") from error
    if isinstance(
        error,
        (PatchRepositoryError, SpatialReservationError, AnalysisRunStoreError),
    ):
        raise HTTPException(
            status_code=503,
            detail="Workbook patching is temporarily unavailable",
        ) from error
    raise error


_NOT_FOUND_CODES = frozenset({"patch_not_found", "payload_chunk_not_found"})


async def _response(
    *,
    proposal: PatchProposal,
    run_id: str,
    user_id: str,
    runs: AnalysisRunAPIService,
) -> PatchProposalResponse:
    run = await runs.get_run(user_id=user_id, run_id=run_id)
    if run is None:
        raise AnalysisRunNotFoundError("analysis run not found")
    return PatchProposalResponse(
        proposal=proposal,
        run=AnalysisRunView.from_run(run),
    )


@router.get("/{run_id}/patch", response_model=PatchProposalResponse)
async def get_current_patch(
    run_id: UUID,
    response: Response,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    repository=Depends(get_patch_repository),
    runs: AnalysisRunAPIService = Depends(get_analysis_run_service),
) -> PatchProposalResponse:
    canonical = str(run_id)
    try:
        proposal = await repository.get_current(
            user_id=user_id,
            run_id=canonical,
        )
        if proposal is None:
            raise PatchNotReadyError(
                "patch_not_found",
                "this run has no patch proposal",
            )
        result = await _response(
            proposal=proposal,
            run_id=canonical,
            user_id=user_id,
            runs=runs,
        )
    except Exception as exc:
        _patch_error(exc)
        raise  # pragma: no cover
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{run_id}/patch/context", response_model=PatchProposalResponse)
async def submit_patch_context(
    run_id: UUID,
    body: PatchContextRequest,
    response: Response,
    trace_id: TraceId = None,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    patches: AnalysisPatchAPIService = Depends(get_analysis_patch_service),
    runs: AnalysisRunAPIService = Depends(get_analysis_run_service),
) -> PatchProposalResponse:
    canonical = str(run_id)
    try:
        proposal = await patches.submit_context(
            user_id=user_id,
            run_id=canonical,
            context=body.context,
            sheet_name_hint=body.sheet_name_hint,
            trace_id=trace_id,
        )
        result = await _response(
            proposal=proposal,
            run_id=canonical,
            user_id=user_id,
            runs=runs,
        )
    except Exception as exc:
        _patch_error(exc)
        raise  # pragma: no cover
    response.headers["Cache-Control"] = "no-store"
    return result


async def _decide(
    *,
    decision: Literal["approve", "reject"],
    run_id: UUID,
    body: PatchDecisionRequest,
    user_id: str,
    patches: AnalysisPatchAPIService,
    runs: AnalysisRunAPIService,
    trace_id: str | None,
) -> PatchProposalResponse:
    canonical = str(run_id)
    proposal = await patches.decide(
        user_id=user_id,
        run_id=canonical,
        command=body.command(decision),
        trace_id=trace_id,
    )
    return await _response(
        proposal=proposal,
        run_id=canonical,
        user_id=user_id,
        runs=runs,
    )


@router.post("/{run_id}/patch/approve", response_model=PatchProposalResponse)
async def approve_patch(
    run_id: UUID,
    body: PatchDecisionRequest,
    response: Response,
    trace_id: TraceId = None,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    patches: AnalysisPatchAPIService = Depends(get_analysis_patch_service),
    runs: AnalysisRunAPIService = Depends(get_analysis_run_service),
) -> PatchProposalResponse:
    try:
        result = await _decide(
            decision="approve",
            run_id=run_id,
            body=body,
            user_id=user_id,
            patches=patches,
            runs=runs,
            trace_id=trace_id,
        )
    except Exception as exc:
        _patch_error(exc)
        raise  # pragma: no cover
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{run_id}/patch/reject", response_model=PatchProposalResponse)
async def reject_patch(
    run_id: UUID,
    body: PatchDecisionRequest,
    response: Response,
    trace_id: TraceId = None,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    patches: AnalysisPatchAPIService = Depends(get_analysis_patch_service),
    runs: AnalysisRunAPIService = Depends(get_analysis_run_service),
) -> PatchProposalResponse:
    try:
        result = await _decide(
            decision="reject",
            run_id=run_id,
            body=body,
            user_id=user_id,
            patches=patches,
            runs=runs,
            trace_id=trace_id,
        )
    except Exception as exc:
        _patch_error(exc)
        raise  # pragma: no cover
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{run_id}/patch/preflight", response_model=PatchPreflightResponse)
async def preflight_patch(
    run_id: UUID,
    body: PatchPreflightRequest,
    response: Response,
    trace_id: TraceId = None,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    patches: AnalysisPatchAPIService = Depends(get_analysis_patch_service),
) -> PatchPreflightResponse:
    try:
        outcome = await patches.preflight(
            user_id=user_id,
            run_id=str(run_id),
            patch_id=body.patch_id,
            patch_revision=body.patch_revision,
            live=body.live(),
            workbook_revision=body.workbook_revision,
            workbook_present=body.workbook_present,
            trace_id=trace_id,
        )
    except Exception as exc:
        _patch_error(exc)
        raise  # pragma: no cover
    response.headers["Cache-Control"] = "no-store"
    return PatchPreflightResponse(
        may_apply=outcome.may_apply,
        conflict=outcome.assessment.kind.value,
        resolution=outcome.assessment.resolution.value,
        message=outcome.assessment.message,
        issue_codes=tuple(
            dict.fromkeys(issue.code for issue in outcome.assessment.issues)
        ),
        proposal=outcome.proposal,
        rebased=outcome.rebased,
    )


@router.post("/{run_id}/patch/receipt", response_model=PatchProposalResponse)
async def record_patch_application(
    run_id: UUID,
    body: PatchApplicationReceipt,
    response: Response,
    trace_id: TraceId = None,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    patches: AnalysisPatchAPIService = Depends(get_analysis_patch_service),
    runs: AnalysisRunAPIService = Depends(get_analysis_run_service),
) -> PatchProposalResponse:
    canonical = str(run_id)
    try:
        proposal = await patches.record_application(
            user_id=user_id,
            run_id=canonical,
            receipt=body,
            trace_id=trace_id,
        )
        result = await _response(
            proposal=proposal,
            run_id=canonical,
            user_id=user_id,
            runs=runs,
        )
    except Exception as exc:
        _patch_error(exc)
        raise  # pragma: no cover
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{run_id}/patch/undo", response_model=PatchProposalResponse)
async def propose_patch_undo(
    run_id: UUID,
    body: PatchUndoRequest,
    response: Response,
    trace_id: TraceId = None,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    patches: AnalysisPatchAPIService = Depends(get_analysis_patch_service),
    runs: AnalysisRunAPIService = Depends(get_analysis_run_service),
) -> PatchProposalResponse:
    canonical = str(run_id)
    try:
        proposal = await patches.propose_undo(
            user_id=user_id,
            run_id=canonical,
            patch_id=body.patch_id,
            patch_revision=body.patch_revision,
            workbook_revision=body.workbook_revision,
            idempotency_key=body.idempotency_key,
            trace_id=trace_id,
        )
        result = await _response(
            proposal=proposal,
            run_id=canonical,
            user_id=user_id,
            runs=runs,
        )
    except Exception as exc:
        _patch_error(exc)
        raise  # pragma: no cover
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get(
    "/{run_id}/patch/{patch_id}/revisions/{revision}"
    "/operations/{op_id}/chunks/{index}"
)
async def download_patch_chunk(
    run_id: UUID,
    patch_id: str,
    revision: int,
    op_id: str,
    index: int,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    patches: AnalysisPatchAPIService = Depends(get_analysis_patch_service),
) -> Response:
    """Stream one payload block through the authenticated boundary.

    Not a signed URL: the tenant check applies to every byte, and nothing
    time-limited has to be persisted in the patch (9.10.3).
    """

    if revision < 1 or index < 0:
        raise HTTPException(status_code=422, detail="Invalid chunk address")
    try:
        data = await patches.read_payload_chunk(
            user_id=user_id,
            run_id=str(run_id),
            patch_id=patch_id,
            patch_revision=revision,
            op_id=op_id,
            index=index,
        )
    except Exception as exc:
        _patch_error(exc)
        raise  # pragma: no cover
    return Response(
        content=data,
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )
