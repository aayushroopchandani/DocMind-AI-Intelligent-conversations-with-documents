"""One authority for deciding what may happen to a validated plan.

Phase 9.1.2 changes the end of planning: a plan that can execute enters the
execution queue instead of completing the run. Phase 9.1 alone does not ship the
engine that drains that queue — it arrives in Phase 9.4 — so the capability
profile carries :attr:`ExecutorCapabilities.native_execution_ready` to say
whether the physical engine is installed.

Without that gate, every successful run parks in ``waiting``/``execution``
forever because nothing consumes the queue. With it, the runtime keeps the
Phase 8 terminal behaviour until the engine exists, and switches to the Phase 9
queue by flipping one flag.

The contract checks below were previously duplicated in the worker, the planning
service and the plan repository, where they could drift apart. Every caller now
routes through :func:`evaluate_admission`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..models.capabilities import (
    CAPABILITY_PROFILE,
    CAPABILITY_PROFILE_VERSION,
    ExecutorCapabilities,
)
from ..models.plans import (
    PLAN_CANONICALIZER_VERSION,
    PLAN_VERSION,
    AnalysisPlan,
    PlanApprovalStatus,
    WorkbookWriteIntent,
)
from .dag import unsupported_operations


class ExecutionAdmission(str, Enum):
    """What the runtime may do with a validated plan."""

    QUEUE = "queue"
    """The contract is current and the native engine is installed."""

    PLAN_ONLY = "plan_only"
    """The contract is current but no engine can execute it yet."""

    REJECT = "reject"
    """The plan is history-only and must never reach an executor."""


@dataclass(frozen=True, slots=True)
class RunAdmissionState:
    """The run facts admission needs, without importing the run service."""

    user_id: str
    workspace_id: str
    current_plan_id: str | None
    current_plan_hash: str | None
    cancellation_requested: bool = False


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    admission: ExecutionAdmission
    code: str | None = None
    message: str | None = None

    @property
    def rejected(self) -> bool:
        return self.admission is ExecutionAdmission.REJECT

    @property
    def queued(self) -> bool:
        return self.admission is ExecutionAdmission.QUEUE


_REJECTION_MESSAGE = (
    "The validated plan does not match the active execution schema, "
    "canonicalization rules, and capability profile. Re-planning is required."
)


def plan_contract_mismatch(plan: AnalysisPlan) -> str | None:
    """Return the field that makes ``plan`` history-only, if any.

    A plan is executable only when every versioned component that produced its
    ``plan_hash`` is still the active one. Anything else stays readable for
    audit but can never enter an executor (Phase 9 invariant 14).
    """

    if plan.plan_version != PLAN_VERSION:
        return "plan_version"
    if plan.capability_profile != CAPABILITY_PROFILE:
        return "capability_profile"
    if plan.capability_version != CAPABILITY_PROFILE_VERSION:
        return "capability_version"
    if plan.canonicalizer_version != PLAN_CANONICALIZER_VERSION:
        return "canonicalizer_version"
    return None


def evaluate_admission(
    plan: AnalysisPlan,
    capabilities: ExecutorCapabilities | None = None,
) -> AdmissionDecision:
    """Decide whether ``plan`` may be queued, only planned, or rejected."""

    mismatch = plan_contract_mismatch(plan)
    if mismatch is not None:
        return AdmissionDecision(
            ExecutionAdmission.REJECT,
            code="plan_execution_admission_rejected",
            message=_REJECTION_MESSAGE,
        )
    profile = capabilities or ExecutorCapabilities()
    if not profile.native_execution_ready:
        return AdmissionDecision(
            ExecutionAdmission.PLAN_ONLY,
            code="native_execution_not_installed",
            message=(
                "The plan is executable, but the native execution engine is "
                "not installed in this deployment."
            ),
        )
    if _writes_workbook(plan) and not profile.workbook_patches_ready:
        # Executing an edit plan would produce a result nothing can apply,
        # because the patch protocol arrives later in Phase 9. Stopping at the
        # plan is the honest outcome.
        return AdmissionDecision(
            ExecutionAdmission.PLAN_ONLY,
            code="workbook_patches_not_installed",
            message=(
                "The plan proposes a workbook edit, and the patch protocol is "
                "not installed in this deployment."
            ),
        )
    unsupported = unsupported_operations(plan)
    if unsupported:
        return AdmissionDecision(
            ExecutionAdmission.PLAN_ONLY,
            code="operation_not_executable",
            message=(
                "The plan is valid, but the native engine cannot execute: "
                + ", ".join(sorted(unsupported))
            ),
        )
    return AdmissionDecision(ExecutionAdmission.QUEUE)


def check_execution_preconditions(
    plan: AnalysisPlan,
    run: RunAdmissionState,
    *,
    capabilities: ExecutorCapabilities | None = None,
) -> AdmissionDecision:
    """Re-check ownership, freshness and lifecycle immediately before running.

    `evaluate_admission` decides what a plan is *allowed* to become at planning
    time. This runs at the execution boundary, where the run may have moved on:
    it may have been cancelled, paused, or superseded by a newer plan while it
    sat in the queue. Phase 9.3.2 requires both.
    """

    if run.user_id != plan.user_id or run.workspace_id != plan.workspace_id:
        return AdmissionDecision(
            ExecutionAdmission.REJECT,
            code="execution_tenant_mismatch",
            message="The run and its plan belong to different tenants.",
        )
    if run.current_plan_id != plan.plan_id or run.current_plan_hash != plan.plan_hash:
        return AdmissionDecision(
            ExecutionAdmission.REJECT,
            code="execution_plan_superseded",
            message="A newer plan replaced the one queued for execution.",
        )
    if run.cancellation_requested:
        return AdmissionDecision(
            ExecutionAdmission.REJECT,
            code="execution_cancelled",
            message="The run was cancelled before execution started.",
        )
    if plan.approval.status is PlanApprovalStatus.REJECTED:
        return AdmissionDecision(
            ExecutionAdmission.REJECT,
            code="execution_plan_rejected",
            message="A rejected plan can never be executed.",
        )
    if plan.approval.status is PlanApprovalStatus.PENDING:
        return AdmissionDecision(
            ExecutionAdmission.REJECT,
            code="execution_awaiting_approval",
            message="The plan still needs approval before it can execute.",
        )
    return evaluate_admission(plan, capabilities)


def _writes_workbook(plan: AnalysisPlan) -> bool:
    return any(
        isinstance(intent, WorkbookWriteIntent) for intent in plan.write_intents
    )


__all__ = [
    "AdmissionDecision",
    "ExecutionAdmission",
    "RunAdmissionState",
    "check_execution_preconditions",
    "evaluate_admission",
    "plan_contract_mismatch",
]
