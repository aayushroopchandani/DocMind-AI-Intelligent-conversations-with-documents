"""Execution-boundary policy shared by planning, persistence and the worker.

Phase 9.1 locks the boundary between "a plan exists" and "a plan may run", and
Phase 9.3 resolves the inputs that boundary admits. Keeping that decision in one
neutral package lets the planning service, the plan repository and the durable
worker agree without any of them depending on each other.

Admission and the plan contracts are imported eagerly because they sit on the
request path. The engine and its orchestration are resolved lazily through
`__getattr__`, so importing this package to check whether a plan may run does
not drag Polars into the API process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .admission import (
    AdmissionDecision,
    ExecutionAdmission,
    RunAdmissionState,
    check_execution_preconditions,
    evaluate_admission,
    plan_contract_mismatch,
)
from .contracts import (
    NATIVE_SUPPORTED_OPERATIONS,
    ExecutionFailureCode,
    ExecutionLimits,
    NativeRecipe,
)
from .dag import CompiledRecipe, RecipeCompilationError, compile_recipe
from .idempotency import execution_key
from .inputs import (
    InputResolutionError,
    MongoNormalizedInputResolver,
    NormalizedInputResolver,
    ResolvedInput,
)

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from .service import (
        ExecutionOutcome,
        ExecutionResultStore,
        NativeExecutionService,
    )

_LAZY = {
    "ExecutionOutcome": "service",
    "ExecutionResultStore": "service",
    "NativeExecutionService": "service",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


__all__ = [
    "NATIVE_SUPPORTED_OPERATIONS",
    "AdmissionDecision",
    "CompiledRecipe",
    "ExecutionAdmission",
    "ExecutionFailureCode",
    "ExecutionLimits",
    "ExecutionOutcome",
    "ExecutionResultStore",
    "InputResolutionError",
    "MongoNormalizedInputResolver",
    "NativeExecutionService",
    "NativeRecipe",
    "NormalizedInputResolver",
    "RecipeCompilationError",
    "ResolvedInput",
    "RunAdmissionState",
    "check_execution_preconditions",
    "compile_recipe",
    "evaluate_admission",
    "execution_key",
    "plan_contract_mismatch",
]
