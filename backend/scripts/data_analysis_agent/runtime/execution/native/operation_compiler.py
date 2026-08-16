"""Compile validated plan steps into lazy Polars stages.

Phase 9.4 caps the native engine at five operations: filter_rows,
select_columns, sort_rows, aggregate and derive_column. Anything else is
rejected here rather than partially supported, so a plan can never half-execute.

Each compiler returns a `LazyFrame`, which lets the stage planner fuse a linear
chain of steps into a single query and leave projection/predicate pushdown to
the engine. Nothing is materialized until the stage boundary.
"""

from __future__ import annotations

import polars as pl

from ...models.plans import (
    AggregateStep,
    DeriveColumnStep,
    FilterRowsStep,
    PlanColumn,
    PlanDataType,
    PlanStep,
    SelectColumnsStep,
    SortRowsStep,
)
from ..contracts import NATIVE_SUPPORTED_OPERATIONS
from . import semantics
from .expression_compiler import (
    ExpressionCompilationError,
    compile_expression,
    strict_zero_divisors,
)
from .schema import polars_dtype


class UnsupportedOperationError(ValueError):
    """The native engine has no executor for this plan step."""


class NativeExecutionSemanticError(ValueError):
    """A declared runtime policy was violated by the actual data."""


# Re-exported so engine code reads the cap from the compiler that enforces it,
# while admission reads the same value without importing Polars.
SUPPORTED_OPERATIONS = NATIVE_SUPPORTED_OPERATIONS

_AGGREGATIONS = {
    "count": lambda column: column.len(),
    "count_distinct": lambda column: column.n_unique(),
    "sum": lambda column: column.sum(),
    "mean": lambda column: column.mean(),
    "median": lambda column: column.median(),
    "minimum": lambda column: column.min(),
    "maximum": lambda column: column.max(),
    "standard_deviation": lambda column: column.std(),
}


def is_supported_step(step: PlanStep) -> bool:
    return step.kind in SUPPORTED_OPERATIONS


def compile_step(
    step: PlanStep,
    frame: pl.LazyFrame,
    *,
    available_columns: frozenset[str],
) -> pl.LazyFrame:
    """Return `frame` with one plan step applied."""

    if isinstance(step, FilterRowsStep):
        return _filter(step, frame, available_columns)
    if isinstance(step, SelectColumnsStep):
        return _select(step, frame, available_columns)
    if isinstance(step, SortRowsStep):
        return _sort(step, frame, available_columns)
    if isinstance(step, DeriveColumnStep):
        return _derive(step, frame, available_columns)
    if isinstance(step, AggregateStep):
        return _aggregate(step, frame, available_columns)
    raise UnsupportedOperationError(
        f"operation '{step.kind}' is not available in the native engine"
    )


def _require_columns(
    keys: tuple[str, ...],
    available: frozenset[str],
    *,
    step_id: str,
) -> None:
    missing = tuple(key for key in keys if key not in available)
    if missing:
        raise ExpressionCompilationError(
            f"step '{step_id}' references unavailable columns: "
            + ", ".join(sorted(missing))
        )


def _filter(
    step: FilterRowsStep,
    frame: pl.LazyFrame,
    available: frozenset[str],
) -> pl.LazyFrame:
    predicate = compile_expression(step.predicate, available_columns=available)
    policy = step.null_predicate_policy
    if policy == "exclude":
        # `fill_null(False)` makes the documented "null predicate is false" rule
        # explicit rather than relying on how the engine drops null masks.
        return frame.filter(predicate.fill_null(False))
    if policy == "include":
        return frame.filter(predicate.fill_null(True))
    # "error" is enforced as a stage precondition, because an expression cannot
    # raise. The guard column is checked when the stage materializes.
    return frame.filter(predicate.fill_null(False))


def null_predicate_guard(step: FilterRowsStep, available: frozenset[str]) -> pl.Expr | None:
    """Return an expression counting rows whose predicate evaluated to null."""

    if step.null_predicate_policy != "error":
        return None
    predicate = compile_expression(step.predicate, available_columns=available)
    return predicate.is_null().sum().alias("null_predicate_rows")


def zero_division_guards(
    step: DeriveColumnStep,
    available: frozenset[str],
) -> tuple[pl.Expr, ...]:
    """Return expressions counting strict divide-by-zero occurrences."""

    return tuple(
        (compile_expression(divisor, available_columns=available) == 0)
        .sum()
        .alias(f"zero_divisor_{index}")
        for index, divisor in enumerate(strict_zero_divisors(step.expression))
    )


def aggregate_null_guards(step: AggregateStep) -> tuple[pl.Expr, ...]:
    """Return expressions counting nulls in metrics declared null-intolerant."""

    return tuple(
        pl.col(metric.input_column_key)
        .null_count()
        .alias(f"aggregate_null_{index}")
        for index, metric in enumerate(step.metrics)
        if metric.null_policy == "error"
    )


def _select(
    step: SelectColumnsStep,
    frame: pl.LazyFrame,
    available: frozenset[str],
) -> pl.LazyFrame:
    _require_columns(step.column_keys, available, step_id=step.step_id)
    return frame.select([pl.col(key) for key in step.column_keys])


def _sort(
    step: SortRowsStep,
    frame: pl.LazyFrame,
    available: frozenset[str],
) -> pl.LazyFrame:
    keys = tuple(key.column_key for key in step.keys)
    _require_columns(keys, available, step_id=step.step_id)
    return frame.sort(
        by=list(keys),
        descending=[key.direction == "descending" for key in step.keys],
        nulls_last=[key.nulls == "last" for key in step.keys],
        # `step.stable` is Literal[True] in the v2 contract; passing it through
        # keeps the intent visible and survives a future widening of the field.
        maintain_order=semantics.SORT_IS_STABLE and step.stable,
    )


def _derive(
    step: DeriveColumnStep,
    frame: pl.LazyFrame,
    available: frozenset[str],
) -> pl.LazyFrame:
    value = compile_expression(step.expression, available_columns=available)
    output = step.output_column
    if step.rounding_scale is not None and output.data_type in _NUMERIC_OUTPUTS:
        value = _round(value, step.rounding_scale, step.rounding_mode)
    return frame.with_columns(
        value.cast(polars_dtype(output.data_type), strict=False).alias(output.key)
    )


_NUMERIC_OUTPUTS = frozenset(
    {
        PlanDataType.NUMBER,
        PlanDataType.DECIMAL,
        PlanDataType.CURRENCY,
        PlanDataType.PERCENTAGE,
    }
)


def _round(value: pl.Expr, scale: int, mode: str) -> pl.Expr:
    if mode == "half_even":
        # Polars' `round` is half-to-even, which is the pinned default.
        return value.round(scale)
    factor = float(10**scale)
    if mode == "floor":
        return (value * factor).floor() / factor
    if mode == "ceiling":
        return (value * factor).ceil() / factor
    if mode == "half_up":
        # Away-from-zero on a tie, applied symmetrically for negatives.
        shifted = value * factor
        return (
            pl.when(shifted < 0)
            .then(-((-shifted) + 0.5).floor())
            .otherwise((shifted + 0.5).floor())
        ) / factor
    raise UnsupportedOperationError(f"unsupported rounding mode '{mode}'")


def _aggregate(
    step: AggregateStep,
    frame: pl.LazyFrame,
    available: frozenset[str],
) -> pl.LazyFrame:
    _require_columns(step.group_by, available, step_id=step.step_id)
    _require_columns(
        tuple(metric.input_column_key for metric in step.metrics),
        available,
        step_id=step.step_id,
    )
    metrics: list[pl.Expr] = []
    for metric in step.metrics:
        function = _AGGREGATIONS.get(metric.function)
        if function is None:
            raise UnsupportedOperationError(
                f"unsupported aggregate function '{metric.function}'"
            )
        column = pl.col(metric.input_column_key)
        if metric.null_policy == "ignore" and metric.function != "count":
            column = column.drop_nulls()
        value = function(column)
        if metric.rounding_scale is not None:
            value = _round(value, metric.rounding_scale, metric.rounding_mode)
        metrics.append(
            value.cast(
                polars_dtype(metric.output_column.data_type),
                strict=False,
            ).alias(metric.output_column.key)
        )
    if not step.group_by:
        return frame.select(metrics)
    grouped = frame.group_by(step.group_by, maintain_order=True).agg(metrics)
    # Group order must not depend on engine scheduling, so the result is sorted
    # by the group keys under the pinned null policy.
    return grouped.sort(
        by=list(step.group_by),
        nulls_last=semantics.NULLS_SORT_LAST_DEFAULT,
        maintain_order=True,
    )


def step_output_columns(step: PlanStep) -> tuple[PlanColumn, ...]:
    """Return the schema a step promises, for post-stage verification."""

    return step.expected_schema


__all__ = [
    "NativeExecutionSemanticError",
    "SUPPORTED_OPERATIONS",
    "UnsupportedOperationError",
    "aggregate_null_guards",
    "compile_step",
    "is_supported_step",
    "null_predicate_guard",
    "step_output_columns",
    "zero_division_guards",
]
