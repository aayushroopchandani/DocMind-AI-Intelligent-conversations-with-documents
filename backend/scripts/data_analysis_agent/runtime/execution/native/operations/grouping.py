"""Operations that reshape rows into groups: aggregate, pivot, unpivot.

9.5 rules implemented here:

* aggregation is a closed function list with declared output names and types;
* a pivot never expands without a bound — categories are either declared in the
  plan or discovered by a bounded, deterministically sorted preflight, and the
  output width is checked before the table is built;
* a pivot cell with no source rows is null for every aggregation. Polars returns
  0 for `sum`, which would make "no data" and "sums to zero" identical;
* unpivot keeps identifier columns explicit and coerces values to one declared
  output type.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from ....models.plans import AggregateStep, PivotStep, UnpivotStep
from .. import semantics
from ..schema import polars_dtype
from .base import (
    NativeExecutionSemanticError,
    Operation,
    register,
    require_columns,
)
from .columns import round_value


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

_PIVOT_AGGREGATIONS = {
    "sum": lambda column: column.sum(),
    "mean": lambda column: column.mean(),
    "min": lambda column: column.min(),
    "max": lambda column: column.max(),
    "count": lambda column: column.len(),
}


# ---------------------------------------------------------------- aggregate


def _metric_expression(metric) -> pl.Expr:
    function = _AGGREGATIONS.get(metric.function)
    if function is None:
        raise NativeExecutionSemanticError(
            f"unsupported aggregate function '{metric.function}'"
        )
    column = pl.col(metric.input_column_key)
    if metric.null_policy == "ignore" and metric.function != "count":
        column = column.drop_nulls()
    value = function(column)
    if metric.function == "sum":
        # Polars sums an all-null group to 0. "No values" is not "zero".
        value = (
            pl.when(pl.col(metric.input_column_key).drop_nulls().len() == 0)
            .then(None)
            .otherwise(value)
        )
    if metric.rounding_scale is not None:
        value = round_value(value, metric.rounding_scale, metric.rounding_mode)
    return value.cast(
        polars_dtype(metric.output_column.data_type),
        strict=False,
    ).alias(metric.output_column.key)


def _apply_aggregate(
    step: AggregateStep,
    frames: Mapping[str, pl.LazyFrame],
) -> pl.LazyFrame:
    frame = frames[step.input_alias]
    available = frozenset(frame.collect_schema().names())
    require_columns(step.group_by, available, step_id=step.step_id)
    require_columns(
        tuple(metric.input_column_key for metric in step.metrics),
        available,
        step_id=step.step_id,
    )
    metrics = [_metric_expression(metric) for metric in step.metrics]
    if not step.group_by:
        return frame.select(metrics)
    grouped = frame.group_by(step.group_by, maintain_order=True).agg(metrics)
    # Group order must not depend on engine scheduling.
    return grouped.sort(
        by=list(step.group_by),
        nulls_last=semantics.NULLS_SORT_LAST_DEFAULT,
        maintain_order=semantics.SORT_IS_STABLE,
    )


def _aggregate_guards(
    step: AggregateStep,
    available: frozenset[str],
) -> tuple[pl.Expr, ...]:
    return tuple(
        pl.col(metric.input_column_key).null_count().alias(f"aggregate_null_{index}")
        for index, metric in enumerate(step.metrics)
        if metric.null_policy == "error"
    )


register(
    Operation(
        kind="aggregate",
        apply=_apply_aggregate,
        guards=_aggregate_guards,
    )
)


# -------------------------------------------------------------------- pivot


def discover_categories(
    frame: pl.DataFrame,
    step: PivotStep,
) -> tuple[object, ...]:
    """Return the pivot's categories under the bounded discovery policy.

    Discovery is deterministic for a given input: the distinct values are sorted
    under the semantic policy and capped. Because the execution key binds every
    input's content signature, a replay rediscovers the same list — which is
    what 9.5 wants from persisting them, without mutating a hashed recipe.
    """

    policy = step.category_policy
    if policy.mode == "explicit":
        return tuple(policy.values)
    distinct = (
        frame.get_column(step.pivot_column)
        .unique()
        .drop_nulls()
        .sort()
        .to_list()
    )
    if len(distinct) > policy.maximum_categories:
        raise NativeExecutionSemanticError(
            f"step '{step.step_id}' discovered {len(distinct)} pivot categories, "
            f"above the declared maximum of {policy.maximum_categories}"
        )
    return tuple(distinct)


def _apply_pivot(
    step: PivotStep,
    frames: Mapping[str, pl.DataFrame],
) -> pl.DataFrame:
    frame = frames[step.input_alias]
    available = frozenset(frame.columns)
    require_columns(
        (*step.index_columns, step.pivot_column, step.value_column),
        available,
        step_id=step.step_id,
    )

    categories = discover_categories(frame, step)
    width = len(step.index_columns) + len(categories)
    if width > step.maximum_output_columns:
        raise NativeExecutionSemanticError(
            f"step '{step.step_id}' would produce {width} columns, above the "
            f"declared maximum of {step.maximum_output_columns}"
        )

    # Restricting to the declared categories before pivoting is what keeps the
    # output width bounded even when the column holds unexpected values.
    selected = frame.filter(pl.col(step.pivot_column).is_in(list(categories)))
    aggregation = _PIVOT_AGGREGATIONS.get(step.aggregation)
    if aggregation is None:
        raise NativeExecutionSemanticError(
            f"unsupported pivot aggregation '{step.aggregation}'"
        )

    grouped = (
        selected.lazy()
        .group_by([*step.index_columns, step.pivot_column], maintain_order=True)
        .agg(aggregation(pl.col(step.value_column)).alias("__pivot_value__"))
        .collect()
    )
    wide = grouped.pivot(
        on=step.pivot_column,
        index=list(step.index_columns),
        values="__pivot_value__",
        aggregate_function=None,
    )

    # Every declared category becomes a column even when no row produced it, so
    # the output schema is a function of the plan rather than of the data.
    value_dtype = _pivot_value_dtype(step)
    missing = [
        pl.lit(None, dtype=value_dtype).alias(_category_key(value))
        for value in categories
        if _category_key(value) not in wide.columns
    ]
    if missing:
        wide = wide.with_columns(missing)
    ordered = [*step.index_columns, *(_category_key(value) for value in categories)]
    return (
        wide.select(ordered)
        .sort(
            by=list(step.index_columns),
            nulls_last=semantics.NULLS_SORT_LAST_DEFAULT,
            maintain_order=semantics.SORT_IS_STABLE,
        )
        .with_columns(
            [
                pl.col(_category_key(value)).cast(value_dtype, strict=False)
                for value in categories
            ]
        )
    )


def _pivot_value_dtype(step: PivotStep) -> pl.DataType:
    result_columns = step.expected_schema[len(step.index_columns) :]
    if result_columns:
        return polars_dtype(result_columns[0].data_type)
    return pl.Float64


def _category_key(value: object) -> str:
    return value if isinstance(value, str) else str(value)


register(Operation(kind="pivot", apply=_apply_pivot, barrier=True))


# ------------------------------------------------------------------ unpivot


def _apply_unpivot(
    step: UnpivotStep,
    frames: Mapping[str, pl.LazyFrame],
) -> pl.LazyFrame:
    frame = frames[step.input_alias]
    available = frozenset(frame.collect_schema().names())
    require_columns(step.id_columns, available, step_id=step.step_id)
    require_columns(step.value_columns, available, step_id=step.step_id)
    return (
        frame.unpivot(
            index=list(step.id_columns),
            on=list(step.value_columns),
            variable_name=step.variable_column.key,
            value_name=step.value_column.key,
        )
        .with_columns(
            [
                pl.col(step.variable_column.key).cast(
                    polars_dtype(step.variable_column.data_type),
                    strict=False,
                ),
                # One declared output type for values gathered from several
                # columns; anything that will not fit becomes null rather than
                # aborting the whole stage.
                pl.col(step.value_column.key).cast(
                    polars_dtype(step.value_column.data_type),
                    strict=False,
                ),
            ]
        )
    )


register(Operation(kind="unpivot", apply=_apply_unpivot))


__all__ = ["discover_categories"]
