"""Operations that shape or produce columns: select, rename, derive, fill.

9.5 rules implemented here:

* explicit casts carry the plan's failure policy — strict, or null-on-failure;
* rounding uses the declared scale and mode, never an engine default;
* ordered fill operations must declare their ordering keys, and a grouped fill
  never carries a value across a group boundary;
* null, empty string and literals like `N/A` stay distinct — the engine does not
  re-derive what counts as missing.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from ....models.plans import (
    DeriveColumnStep,
    FillMissingStep,
    FillRule,
    PlanColumn,
    PlanDataType,
    RenameColumnsStep,
    SelectColumnsStep,
)
from .. import semantics
from ..expression_compiler import compile_expression, strict_zero_divisors
from ..schema import polars_dtype
from .base import (
    NativeExecutionSemanticError,
    Operation,
    register,
    require_columns,
)


NUMERIC_OUTPUTS = frozenset(
    {
        PlanDataType.NUMBER,
        PlanDataType.DECIMAL,
        PlanDataType.CURRENCY,
        PlanDataType.PERCENTAGE,
    }
)


def round_value(value: pl.Expr, scale: int, mode: str) -> pl.Expr:
    """Round under the declared mode. Polars' own `round` is half-to-even."""

    if mode == "half_even":
        return value.round(scale)
    factor = float(10**scale)
    shifted = value * factor
    if mode == "floor":
        return shifted.floor() / factor
    if mode == "ceiling":
        return shifted.ceil() / factor
    if mode == "half_up":
        # Away from zero on a tie, applied symmetrically for negatives.
        return (
            pl.when(shifted < 0)
            .then(-((-shifted) + 0.5).floor())
            .otherwise((shifted + 0.5).floor())
        ) / factor
    raise NativeExecutionSemanticError(f"unsupported rounding mode '{mode}'")


# ------------------------------------------------------------------- select


def _apply_select(
    step: SelectColumnsStep,
    frames: Mapping[str, pl.LazyFrame],
) -> pl.LazyFrame:
    frame = frames[step.input_alias]
    require_columns(
        step.column_keys,
        frozenset(frame.collect_schema().names()),
        step_id=step.step_id,
    )
    return frame.select([pl.col(key) for key in step.column_keys])


register(Operation(kind="select_columns", apply=_apply_select))


# ------------------------------------------------------------------- rename


def _apply_rename(
    step: RenameColumnsStep,
    frames: Mapping[str, pl.LazyFrame],
) -> pl.LazyFrame:
    frame = frames[step.input_alias]
    available = frozenset(frame.collect_schema().names())
    require_columns(
        tuple(item.source_key for item in step.renames),
        available,
        step_id=step.step_id,
    )
    mapping = {item.source_key: item.output_key for item in step.renames}
    survivors = {
        name for name in available if name not in mapping
    }
    collisions = sorted(set(mapping.values()).intersection(survivors))
    if collisions:
        raise NativeExecutionSemanticError(
            f"step '{step.step_id}' renames onto existing columns: "
            + ", ".join(collisions)
        )
    outputs = list(mapping.values())
    if len(outputs) != len(set(outputs)):
        raise NativeExecutionSemanticError(
            f"step '{step.step_id}' renames two columns to the same key"
        )
    return frame.rename(mapping)


register(Operation(kind="rename_columns", apply=_apply_rename))


# ------------------------------------------------------------------- derive


def _apply_derive(
    step: DeriveColumnStep,
    frames: Mapping[str, pl.LazyFrame],
) -> pl.LazyFrame:
    frame = frames[step.input_alias]
    available = frozenset(frame.collect_schema().names())
    value = compile_expression(step.expression, available_columns=available)
    output = step.output_column
    if step.rounding_scale is not None and output.data_type in NUMERIC_OUTPUTS:
        value = round_value(value, step.rounding_scale, step.rounding_mode)
    strict = step.overflow_policy == "error"
    return frame.with_columns(
        value.cast(polars_dtype(output.data_type), strict=strict).alias(output.key)
    )


def _derive_guards(
    step: DeriveColumnStep,
    available: frozenset[str],
) -> tuple[pl.Expr, ...]:
    return tuple(
        (compile_expression(divisor, available_columns=available) == 0)
        .sum()
        .alias(f"zero_divisor_{index}")
        for index, divisor in enumerate(strict_zero_divisors(step.expression))
    )


register(
    Operation(
        kind="derive_column",
        apply=_apply_derive,
        guards=_derive_guards,
    )
)


# ------------------------------------------------------------- fill missing


def _fill_expression(
    rule: FillRule,
    column: PlanColumn | None,
    step: FillMissingStep,
) -> pl.Expr:
    source = pl.col(rule.column_key)
    strategy = rule.strategy

    if strategy == "constant":
        dtype = polars_dtype(column.data_type) if column is not None else None
        return source.fill_null(pl.lit(rule.value, dtype=dtype))

    if strategy in {"forward_fill", "backward_fill"}:
        filled = (
            source.forward_fill()
            if strategy == "forward_fill"
            else source.backward_fill()
        )
        # A directional fill inside groups must not carry a value across a
        # group boundary, which is exactly what `over` prevents.
        return filled.over(step.group_by) if step.group_by else filled

    statistic = {
        "mean": source.mean(),
        "median": source.median(),
        "mode": source.mode().first(),
    }[strategy]
    if step.group_by:
        statistic = statistic.over(step.group_by)
    return source.fill_null(statistic)


def _apply_fill(
    step: FillMissingStep,
    frames: Mapping[str, pl.LazyFrame],
) -> pl.LazyFrame:
    frame = frames[step.input_alias]
    available = frozenset(frame.collect_schema().names())
    require_columns(
        tuple(rule.column_key for rule in step.rules),
        available,
        step_id=step.step_id,
    )
    require_columns(step.group_by, available, step_id=step.step_id)
    require_columns(
        tuple(key.column_key for key in step.order_by),
        available,
        step_id=step.step_id,
    )

    schema = {column.key: column for column in step.expected_schema}
    ordered = frame
    if step.order_by:
        # The plan contract already requires order_by for directional fills;
        # applying it here is what makes "previous row" mean something fixed.
        ordered = frame.sort(
            by=[key.column_key for key in step.order_by],
            descending=[key.direction == "descending" for key in step.order_by],
            nulls_last=[key.nulls == "last" for key in step.order_by],
            maintain_order=semantics.SORT_IS_STABLE,
        )
    return ordered.with_columns(
        [
            _fill_expression(rule, schema.get(rule.column_key), step).alias(
                rule.column_key
            )
            for rule in step.rules
        ]
    )


register(Operation(kind="fill_missing", apply=_apply_fill))


__all__ = ["NUMERIC_OUTPUTS", "round_value"]
