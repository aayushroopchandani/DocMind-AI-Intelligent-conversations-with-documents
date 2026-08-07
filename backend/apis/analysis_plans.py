"""Tenant-scoped inspection and HITL decisions for validated analysis plans."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from apis.analysis_runs import AnalysisRunAPIService, AnalysisRunView, get_analysis_run_service
from apis.deps import current_user_id, verify_internal_secret
from scripts.data_analysis_agent.runtime.models.plans import (
    AnalysisPlan,
    PlanApprovalCommand,
    PlanRejectionReason,
    WorkbookVersionGuard,
)
from scripts.data_analysis_agent.runtime.planning.service import AnalysisPlanningService
from scripts.data_analysis_agent.runtime.repositories.plans import (
    AnalysisPlanConflictError,
    AnalysisPlanNotFoundError,
    AnalysisPlanRepositoryError,
)
from scripts.data_analysis_agent.runtime.repositories.runs import (
    AnalysisRunNotFoundError,
    AnalysisRunStoreError,
)


router = APIRouter(prefix="/analysis/runs", tags=["analysis-plans"])
TraceId = Annotated[
    str | None,
    Header(
        alias="X-Request-ID",
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$",
    ),
]


class AnalysisPlanAPIService(Protocol):
    async def get_current_plan(self, *, user_id: str, run_id: str) -> AnalysisPlan: ...

    async def decide_plan(
        self,
        *,
        user_id: str,
        run_id: str,
        command: PlanApprovalCommand,
        trace_id: str | None = None,
    ) -> AnalysisPlan: ...


def get_analysis_planning_service(request: Request) -> AnalysisPlanAPIService:
    service = getattr(request.app.state, "analysis_planning_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis planning is unavailable",
        )
    return cast(AnalysisPlanningService, service)


class AnalysisPlanDecisionRequest(BaseModel):
    approval_type: Literal["plan"] = "plan"
    plan_id: str = Field(min_length=36, max_length=36)
    plan_revision: int = Field(ge=1)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    workbook_guards: tuple[WorkbookVersionGuard, ...] = Field(default=(), max_length=24)
    decision_id: str = Field(min_length=36, max_length=36)
    comment: str | None = Field(default=None, max_length=1_000)
    reason: PlanRejectionReason | None = None

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    def command(self, decision: Literal["approve", "reject"]) -> PlanApprovalCommand:
        if decision == "approve" and self.reason is not None:
            raise ValueError("approval cannot include a rejection reason")
        return PlanApprovalCommand(
            decision=decision,
            plan_id=self.plan_id,
            expected_revision=self.plan_revision,
            expected_plan_hash=self.plan_hash,
            expected_input_signature=self.input_signature,
            workbook_guards=self.workbook_guards,
            comment=self.comment,
            rejection_reason=self.reason,
            decision_id=self.decision_id,
        )


class AnalysisPlanResponse(BaseModel):
    plan: AnalysisPlan
    run: AnalysisRunView

    model_config = ConfigDict(extra="forbid", frozen=True)


def _plan_error(error: Exception) -> None:
    if isinstance(error, (AnalysisPlanNotFoundError, AnalysisRunNotFoundError)):
        raise HTTPException(status_code=404, detail="Analysis plan not found") from error
    if isinstance(error, (AnalysisPlanConflictError, ValueError)):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, (AnalysisPlanRepositoryError, AnalysisRunStoreError)):
        raise HTTPException(
            status_code=503,
            detail="Analysis planning is temporarily unavailable",
        ) from error
    raise error


async def _response(
    *,
    plan: AnalysisPlan,
    run_id: str,
    user_id: str,
    run_service: AnalysisRunAPIService,
) -> AnalysisPlanResponse:
    run = await run_service.get_run(user_id=user_id, run_id=run_id)
    if run is None:
        raise AnalysisRunNotFoundError("analysis run not found")
    return AnalysisPlanResponse(plan=plan, run=AnalysisRunView.from_run(run))


@router.get("/{run_id}/plan", response_model=AnalysisPlanResponse)
async def get_analysis_plan(
    run_id: UUID,
    response: Response,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    planning: AnalysisPlanAPIService = Depends(get_analysis_planning_service),
    runs: AnalysisRunAPIService = Depends(get_analysis_run_service),
) -> AnalysisPlanResponse:
    canonical = str(run_id)
    try:
        result = await _response(
            plan=await planning.get_current_plan(user_id=user_id, run_id=canonical),
            run_id=canonical,
            user_id=user_id,
            run_service=runs,
        )
    except Exception as exc:
        _plan_error(exc)
        raise  # pragma: no cover
    response.headers["Cache-Control"] = "no-store"
    return result


async def _decide(
    *,
    decision: Literal["approve", "reject"],
    run_id: UUID,
    body: AnalysisPlanDecisionRequest,
    user_id: str,
    planning: AnalysisPlanAPIService,
    runs: AnalysisRunAPIService,
    trace_id: str | None,
) -> AnalysisPlanResponse:
    canonical = str(run_id)
    plan = await planning.decide_plan(
        user_id=user_id,
        run_id=canonical,
        command=body.command(decision),
        trace_id=trace_id,
    )
    return await _response(
        plan=plan,
        run_id=canonical,
        user_id=user_id,
        run_service=runs,
    )


@router.post("/{run_id}/approve", response_model=AnalysisPlanResponse)
async def approve_analysis_plan(
    run_id: UUID,
    body: AnalysisPlanDecisionRequest,
    response: Response,
    trace_id: TraceId = None,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    planning: AnalysisPlanAPIService = Depends(get_analysis_planning_service),
    runs: AnalysisRunAPIService = Depends(get_analysis_run_service),
) -> AnalysisPlanResponse:
    try:
        result = await _decide(
            decision="approve",
            run_id=run_id,
            body=body,
            user_id=user_id,
            planning=planning,
            runs=runs,
            trace_id=trace_id,
        )
    except Exception as exc:
        _plan_error(exc)
        raise  # pragma: no cover
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{run_id}/reject", response_model=AnalysisPlanResponse)
async def reject_analysis_plan(
    run_id: UUID,
    body: AnalysisPlanDecisionRequest,
    response: Response,
    trace_id: TraceId = None,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    planning: AnalysisPlanAPIService = Depends(get_analysis_planning_service),
    runs: AnalysisRunAPIService = Depends(get_analysis_run_service),
) -> AnalysisPlanResponse:
    try:
        result = await _decide(
            decision="reject",
            run_id=run_id,
            body=body,
            user_id=user_id,
            planning=planning,
            runs=runs,
            trace_id=trace_id,
        )
    except Exception as exc:
        _plan_error(exc)
        raise  # pragma: no cover
    response.headers["Cache-Control"] = "no-store"
    return result
