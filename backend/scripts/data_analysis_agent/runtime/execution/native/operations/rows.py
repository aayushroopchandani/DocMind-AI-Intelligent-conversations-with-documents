"""Operations that select or order rows: filter, sort, deduplicate.

9.5 rules implemented here:

* a null predicate result is false unless the plan selects another policy;
* sorting is stable with per-key direction and null placement;
* "first" and "last" in deduplication refer to a declared deterministic order —
  input order under `stable_input`, the declared sort keys otherwise.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from ....models.plans import DeduplicateStep, FilterRowsStep, SortRowsStep
from .. import semantics
from ..expression_compiler import compile_expression
from .base import Operation, register, require_columns


def _sort_arguments(keys) -> dict[str, object]:
    return {
        "by": [key.column_key for key in keys],
        "descending": [key.direction == "descending" for key in keys],
        "nulls_last": [key.nulls == "last" for key in keys],
        "maintain_order": semantics.SORT_IS_STABLE,
    }


# ------------------------------------------------------------------- filter


def _apply_filter(
    step: FilterRowsStep,
    frames: Mapping[str, pl.LazyFrame],
) -> pl.LazyFrame:
    frame = frames[step.input_alias]
    available = frozenset(frame.collect_schema().names())
    predicate = compile_expression(step.predicate, available_columns=available)
    # `fill_null` states the policy outright instead of depending on how the
    # engine happens to treat a null mask.
    keep_null = step.null_predicate_policy == "include"
    return frame.filter(predicate.fill_null(keep_null))


def _filter_guards(
    step: FilterRowsStep,
    available: frozenset[str],
) -> tuple[pl.Expr, ...]:
    if step.null_predicate_policy != "error":
        return ()
    predicate = compile_expression(step.predicate, available_columns=available)
    return (predicate.is_null().sum().alias("null_predicate_rows"),)


register(
    Operation(
        kind="filter_rows",
        apply=_apply_filter,
        guards=_filter_guards,
    )
)


# --------------------------------------------------------------------- sort


def _apply_sort(
    step: SortRowsStep,
    frames: Mapping[str, pl.LazyFrame],
) -> pl.LazyFrame:
    frame = frames[step.input_alias]
    available = frozenset(frame.collect_schema().names())
    require_columns(
        tuple(key.column_key for key in step.keys),
        available,
        step_id=step.step_id,
    )
    return frame.sort(**_sort_arguments(step.keys))


register(Operation(kind="sort_rows", apply=_apply_sort))


# -------------------------------------------------------------- deduplicate


def _apply_deduplicate(
    step: DeduplicateStep,
    frames: Mapping[str, pl.LazyFrame],
) -> pl.LazyFrame:
    frame = frames[step.input_alias]
    available = frozenset(frame.collect_schema().names())
    require_columns(step.key_columns, available, step_id=step.step_id)

    if step.order_policy == "sort_keys":
        require_columns(
            tuple(key.column_key for key in step.order_by),
            available,
            step_id=step.step_id,
        )
        # Sorting first makes "first" and "last" refer to the declared order
        # rather than to however the rows happened to arrive.
        frame = frame.sort(**_sort_arguments(step.order_by))

    # `keep="error"` is enforced by the guard below; the frame still has to
    # produce something, and keeping the first row is the harmless choice.
    keep = "first" if step.keep == "error" else step.keep
    return frame.unique(
        subset=list(step.key_columns),
        keep=keep,
        maintain_order=semantics.ROW_ORDER_IS_INPUT_ORDER,
    )


def _deduplicate_guards(
    step: DeduplicateStep,
    available: frozenset[str],
) -> tuple[pl.Expr, ...]:
    if step.keep != "error":
        return ()
    # Rows that would be discarded: total minus the number of distinct keys.
    return (
        (pl.len() - pl.struct(list(step.key_columns)).n_unique())
        .alias("duplicate_rows"),
    )


register(
    Operation(
        kind="deduplicate",
        apply=_apply_deduplicate,
        guards=_deduplicate_guards,
    )
)


__all__: list[str] = []
