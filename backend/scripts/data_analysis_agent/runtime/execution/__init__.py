"""Execution-boundary policy shared by planning, persistence and the worker.

Phase 9.1 locks the boundary between "a plan exists" and "a plan may run".
Keeping that decision in one neutral package lets the planning service, the plan
repository and the durable worker agree without any of them depending on each
other.
"""

from .admission import (
    AdmissionDecision,
    ExecutionAdmission,
    evaluate_admission,
    plan_contract_mismatch,
)

__all__ = [
    "AdmissionDecision",
    "ExecutionAdmission",
    "evaluate_admission",
    "plan_contract_mismatch",
]
