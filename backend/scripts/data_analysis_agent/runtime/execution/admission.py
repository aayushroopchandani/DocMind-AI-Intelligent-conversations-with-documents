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
)


class ExecutionAdmission(str, Enum):
    """What the runtime may do with a validated plan."""

    QUEUE = "queue"
    """The contract is current and the native engine is installed."""

    PLAN_ONLY = "plan_only"
    """The contract is current but no engine can execute it yet."""

    REJECT = "reject"
    """The plan is history-only and must never reach an executor."""


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
    return AdmissionDecision(ExecutionAdmission.QUEUE)


__all__ = [
    "AdmissionDecision",
    "ExecutionAdmission",
    "evaluate_admission",
    "plan_contract_mismatch",
]
