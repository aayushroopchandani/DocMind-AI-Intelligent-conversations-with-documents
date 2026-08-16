"""Compile a validated plan into an executable native recipe.

The plan validator already proved the step graph is acyclic and every alias is
produced before it is read. This module does the part that is specific to
execution: decide which steps the native engine can actually run, order them
into a dependency-respecting sequence, and identify the single alias that is the
run's result.

If any step is outside the capped operation set the whole compilation fails.
A plan never half-executes.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models.plans import AnalysisPlan, PlanStep, step_input_aliases
from .contracts import NATIVE_SUPPORTED_OPERATIONS, ExecutionFailureCode


class RecipeCompilationError(RuntimeError):
    """A validated plan cannot be turned into a native recipe."""

    def __init__(self, code: ExecutionFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class CompiledRecipe:
    """Ordered native steps plus the alias that carries the run's result."""

    steps: tuple[PlanStep, ...]
    result_alias: str
    input_aliases: tuple[str, ...]


def unsupported_operations(plan: AnalysisPlan) -> tuple[str, ...]:
    """Return the distinct step kinds the native engine cannot execute."""

    return tuple(
        dict.fromkeys(
            step.kind
            for step in plan.steps
            if step.kind not in NATIVE_SUPPORTED_OPERATIONS
        )
    )


def compile_recipe(plan: AnalysisPlan) -> CompiledRecipe:
    """Return the executable recipe for `plan`, or raise a typed error."""

    unsupported = unsupported_operations(plan)
    if unsupported:
        raise RecipeCompilationError(
            ExecutionFailureCode.UNSUPPORTED_OPERATION,
            "the native engine cannot execute: " + ", ".join(sorted(unsupported)),
        )
    steps = _topological_order(plan)
    return CompiledRecipe(
        steps=steps,
        result_alias=_result_alias(steps),
        input_aliases=tuple(dataset.alias for dataset in plan.input_datasets),
    )


def _topological_order(plan: AnalysisPlan) -> tuple[PlanStep, ...]:
    """Order steps so every alias is produced before it is consumed.

    Plans normally arrive already ordered by canonicalization; this makes the
    executor independent of that rather than trusting it.
    """

    remaining = list(plan.steps)
    available = {dataset.alias for dataset in plan.input_datasets}
    ordered: list[PlanStep] = []
    while remaining:
        ready = [
            step
            for step in remaining
            if all(alias in available for alias in step_input_aliases(step))
        ]
        if not ready:
            blocked = ", ".join(sorted(step.step_id for step in remaining))
            raise RecipeCompilationError(
                ExecutionFailureCode.COMPILATION_FAILED,
                f"steps cannot be ordered for execution: {blocked}",
            )
        for step in ready:
            ordered.append(step)
            available.add(step.output_alias)
            remaining.remove(step)
    return tuple(ordered)


def _result_alias(steps: tuple[PlanStep, ...]) -> str:
    """Return the alias no other step consumes — the run's output."""

    consumed = {alias for step in steps for alias in step_input_aliases(step)}
    terminals = tuple(
        step.output_alias for step in steps if step.output_alias not in consumed
    )
    if len(terminals) != 1:
        raise RecipeCompilationError(
            ExecutionFailureCode.COMPILATION_FAILED,
            "a native plan must produce exactly one terminal result, "
            f"found {len(terminals)}",
        )
    return terminals[0]


__all__ = [
    "CompiledRecipe",
    "RecipeCompilationError",
    "compile_recipe",
    "unsupported_operations",
]
