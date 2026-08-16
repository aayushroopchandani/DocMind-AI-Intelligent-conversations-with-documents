"""The shape every native operation implements.

Most operations are lazy: they return a `LazyFrame` so a linear chain fuses into
one query and Polars does its own pushdown. Two things break that:

* an operation whose engine API is eager (pivot), and
* a policy the plan declares as an error, which an expression cannot raise from.

Both are modelled explicitly rather than hidden. A `barrier` operation asks the
engine to materialize its inputs first, which also lets it raise directly. A
`guards` builder returns counter expressions that the engine evaluates against
the step's input alongside the stage, and turns a non-zero count into a typed
failure before anything is published.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import polars as pl

from ....models.plans import PlanStep


class UnsupportedOperationError(ValueError):
    """The native engine has no executor for this plan step."""


class NativeExecutionSemanticError(ValueError):
    """A declared runtime policy was violated by the actual data."""


LazyApply = Callable[[PlanStep, Mapping[str, pl.LazyFrame]], pl.LazyFrame]
EagerApply = Callable[[PlanStep, Mapping[str, pl.DataFrame]], pl.DataFrame]
GuardBuilder = Callable[[PlanStep, frozenset[str]], tuple[pl.Expr, ...]]


@dataclass(frozen=True, slots=True)
class Operation:
    """One executable plan-step kind."""

    kind: str
    apply: LazyApply | EagerApply
    barrier: bool = False
    """True when the engine must collect this step's inputs before applying it.
    A barrier operation runs eagerly and may raise its own typed errors."""
    guards: GuardBuilder | None = None
    """Counters for policies the plan declared as `error`, evaluated against the
    step's primary input."""


_REGISTRY: dict[str, Operation] = {}


def register(operation: Operation) -> Operation:
    if operation.kind in _REGISTRY:  # pragma: no cover - import-time guard
        raise RuntimeError(f"operation '{operation.kind}' is already registered")
    _REGISTRY[operation.kind] = operation
    return operation


def lookup(kind: str) -> Operation:
    try:
        return _REGISTRY[kind]
    except KeyError:
        raise UnsupportedOperationError(
            f"operation '{kind}' is not available in the native engine"
        ) from None


def registered_kinds() -> frozenset[str]:
    return frozenset(_REGISTRY)


def require_columns(
    keys: tuple[str, ...],
    available: frozenset[str],
    *,
    step_id: str,
) -> None:
    """Raise when a step names a column its input does not have."""

    missing = tuple(key for key in keys if key not in available)
    if missing:
        raise NativeExecutionSemanticError(
            f"step '{step_id}' references unavailable columns: "
            + ", ".join(sorted(missing))
        )


__all__ = [
    "EagerApply",
    "GuardBuilder",
    "LazyApply",
    "NativeExecutionSemanticError",
    "Operation",
    "UnsupportedOperationError",
    "lookup",
    "register",
    "registered_kinds",
    "require_columns",
]
