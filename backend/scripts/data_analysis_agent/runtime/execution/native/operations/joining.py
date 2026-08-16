"""The join operation — the one that can multiply a table by accident.

9.5 rules implemented here:

* explicit left/right keys and join kind;
* null keys never match, so two rows with missing keys are not "equal";
* the declared cardinality is validated by the engine, not assumed;
* column collisions are resolved by the plan's declared suffixes, and keys that
  share a name on both sides coalesce into one column;
* the expansion ratio is bounded — enforced by the engine after the row counts
  are known, because a join bomb is only visible once the sizes are.

`join_output_schema` in the plan model already derives the output schema from
the suffix policy. This module applies exactly the same rule so the executed
shape matches the validated one.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from ....models.plans import JoinStep, join_output_schema
from .base import NativeExecutionSemanticError, Operation, register, require_columns


_CARDINALITY_VALIDATION = {
    "one_to_one": "1:1",
    "one_to_many": "1:m",
    "many_to_one": "m:1",
    "many_to_many": "m:m",
}


def _apply_join(
    step: JoinStep,
    frames: Mapping[str, pl.LazyFrame],
) -> pl.LazyFrame:
    left = frames[step.left_alias]
    right = frames[step.right_alias]
    left_columns = frozenset(left.collect_schema().names())
    right_columns = frozenset(right.collect_schema().names())
    require_columns(
        tuple(pair.left_column_key for pair in step.keys),
        left_columns,
        step_id=step.step_id,
    )
    require_columns(
        tuple(pair.right_column_key for pair in step.keys),
        right_columns,
        step_id=step.step_id,
    )

    # Keys sharing a name on both sides coalesce; every other shared name is a
    # collision the declared suffixes must resolve.
    coalesced = {
        pair.left_column_key
        for pair in step.keys
        if pair.left_column_key == pair.right_column_key
    }
    collisions = left_columns.intersection(right_columns).difference(coalesced)
    renames_left = {name: f"{name}{step.left_suffix}" for name in collisions}
    renames_right = {name: f"{name}{step.right_suffix}" for name in collisions}
    unresolved = sorted(
        set(renames_left.values()).intersection(right_columns)
        | set(renames_right.values()).intersection(left_columns)
    )
    if unresolved:
        raise NativeExecutionSemanticError(
            f"step '{step.step_id}' cannot resolve column collisions with the "
            "declared suffixes: " + ", ".join(unresolved)
        )
    if renames_left:
        left = left.rename(renames_left)
    if renames_right:
        right = right.rename(renames_right)

    left_on = [
        renames_left.get(pair.left_column_key, pair.left_column_key)
        for pair in step.keys
    ]
    right_on = [
        renames_right.get(pair.right_column_key, pair.right_column_key)
        for pair in step.keys
    ]

    joined = left.join(
        right,
        left_on=left_on,
        right_on=right_on,
        how=step.join_type,
        # `nulls_match` is Literal[False] in the plan contract: a missing key is
        # unknown, not a value that equals another missing key.
        nulls_equal=step.nulls_match,
        validate=_CARDINALITY_VALIDATION[step.expected_cardinality],
        coalesce=True,
        maintain_order="left",
    )
    return joined.select(
        [pl.col(column.key) for column in _output_keys(step, left, right, coalesced)]
    )


def _output_keys(step: JoinStep, left, right, coalesced):
    """Return the declared output schema, so column order is the plan's."""

    return step.expected_schema


def expansion_ratio(input_rows: int, output_rows: int) -> float:
    """Return how much a join grew its larger input."""

    if input_rows <= 0:
        return 1.0 if output_rows == 0 else float("inf")
    return output_rows / input_rows


def check_expansion(step: JoinStep, input_rows: int, output_rows: int) -> None:
    """Raise when a join produced more rows than the plan allowed.

    Checked after the counts are known rather than estimated beforehand: an
    estimate cannot see the actual key distribution, and this runs before any
    result is hashed or published.
    """

    ratio = expansion_ratio(input_rows, output_rows)
    if ratio > step.maximum_expansion_ratio:
        raise NativeExecutionSemanticError(
            f"step '{step.step_id}' expanded {input_rows} rows to {output_rows} "
            f"(ratio {ratio:.2f}), above the declared maximum of "
            f"{step.maximum_expansion_ratio:.2f}"
        )


register(Operation(kind="join", apply=_apply_join))


__all__ = ["check_expansion", "expansion_ratio", "join_output_schema"]
