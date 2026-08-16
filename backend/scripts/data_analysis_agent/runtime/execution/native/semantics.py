"""The pinned semantic policy every native execution runs under (Phase 9.5).

The rule this module exists to enforce: *never rely on an engine default where
the default affects the result*. Polars has several, and they are not always the
answer a spreadsheet user expects. The clearest example is pivot — summing an
empty cell yields `0` while every other aggregation yields null, so "no rows
here" and "the rows here sum to zero" become indistinguishable. This policy pins
that to null and the compilers implement it explicitly.

`NATIVE_SEMANTICS_VERSION` is part of the execution key. Changing any value in
this module must bump it, so a cached result can never be reused under different
semantics (9.5 acceptance criteria).
"""

from __future__ import annotations

from typing import Final


NATIVE_SEMANTICS_VERSION: Final = "2.0"
"""2.0 widened the policy from five operations to the full native set."""


# ---------------------------------------------------------------- global scope

TIMEZONE: Final = "UTC"
LOCALE: Final = "en-US"

DATE_INPUT_FORMAT: Final = "iso-8601"
"""Dates are parsed as ISO calendar dates only. Ambiguous regional forms such as
`03/04/2026` are rejected rather than guessed; Phase 7 normalization is where a
source's date convention is resolved."""

DECIMAL_SCALE: Final = 6
"""Default scale for a derived numeric value with no declared rounding."""

ROUNDING_MODE: Final = "half_even"
"""Banker's rounding, so repeated aggregation does not drift upward."""

INTEGER_OVERFLOW_POLICY: Final = "widen_to_float"
"""Integer arithmetic that would exceed 64 bits widens rather than wrapping.
Silent wraparound is the one outcome a financial table must never produce."""

STRING_COMPARISON_IS_CASE_SENSITIVE: Final = True
STRING_NORMALIZATION: Final = "none"
"""Text is compared byte-for-byte. Case folding and Unicode normalization belong
to Phase 7, which knows the source's conventions; redoing it here would make the
same value compare differently depending on where it entered the pipeline."""

EMPTY_STRING_IS_NOT_NULL: Final = True
"""Phase 7 normalization already decided which markers mean "missing", so an
empty string that reaches the engine is an empty string, and a literal such as
`N/A` is text. The engine never re-derives missing-value markers."""

ALLOW_NON_FINITE_FLOATS: Final = False
"""NaN and infinity never reach a published result; they fail validation."""


# ------------------------------------------------------------- row ordering

SORT_IS_STABLE: Final = True
NULLS_SORT_LAST_DEFAULT: Final = True

ROW_ORDER_IS_INPUT_ORDER: Final = True
"""Every operation that has to break a tie — deduplication's keep-first and
keep-last, group ordering — resolves it by position in the input table. Polars'
`maintain_order=True` provides this without a materialized ordinal column, so
the engine asks for it explicitly everywhere it matters."""


# --------------------------------------------------------- column collisions

JOIN_COLLISION_POLICY: Final = "explicit_suffix"
"""A column present on both sides of a join is renamed with the plan's declared
suffixes. Join keys with the same name on both sides coalesce into one column.
Any collision the suffixes fail to resolve fails the step."""

MAXIMUM_COLUMN_KEY_LENGTH: Final = 120


# ------------------------------------------------------------ missing values

PIVOT_EMPTY_CELL: Final = "null"
"""A pivot cell with no source rows is null for every aggregation, including
sum. Polars returns 0 for sum, which would make "no data" indistinguishable
from "sums to zero"."""

AGGREGATE_EMPTY_GROUP_SUM: Final = "null"
"""Same rule for an aggregate over a group whose values are all null."""


# ----------------------------------------------------------------- functions

AGGREGATE_FUNCTIONS: Final = (
    "count",
    "count_distinct",
    "sum",
    "mean",
    "median",
    "minimum",
    "maximum",
    "standard_deviation",
)
"""The closed list. Bounded quantiles are named by 9.5 but are not in the Phase
8 plan contract, so they are not accepted here either."""


def semantics_fingerprint() -> dict[str, object]:
    """Return the policy as canonical content for the execution key."""

    return {
        "native_semantics_version": NATIVE_SEMANTICS_VERSION,
        "timezone": TIMEZONE,
        "locale": LOCALE,
        "date_input_format": DATE_INPUT_FORMAT,
        "decimal_scale": DECIMAL_SCALE,
        "rounding_mode": ROUNDING_MODE,
        "integer_overflow_policy": INTEGER_OVERFLOW_POLICY,
        "string_comparison_is_case_sensitive": STRING_COMPARISON_IS_CASE_SENSITIVE,
        "string_normalization": STRING_NORMALIZATION,
        "empty_string_is_not_null": EMPTY_STRING_IS_NOT_NULL,
        "allow_non_finite_floats": ALLOW_NON_FINITE_FLOATS,
        "sort_is_stable": SORT_IS_STABLE,
        "nulls_sort_last_default": NULLS_SORT_LAST_DEFAULT,
        "row_order_is_input_order": ROW_ORDER_IS_INPUT_ORDER,
        "join_collision_policy": JOIN_COLLISION_POLICY,
        "maximum_column_key_length": MAXIMUM_COLUMN_KEY_LENGTH,
        "pivot_empty_cell": PIVOT_EMPTY_CELL,
        "aggregate_empty_group_sum": AGGREGATE_EMPTY_GROUP_SUM,
        "aggregate_functions": list(AGGREGATE_FUNCTIONS),
    }


__all__ = [
    "AGGREGATE_EMPTY_GROUP_SUM",
    "AGGREGATE_FUNCTIONS",
    "ALLOW_NON_FINITE_FLOATS",
    "DATE_INPUT_FORMAT",
    "DECIMAL_SCALE",
    "EMPTY_STRING_IS_NOT_NULL",
    "INTEGER_OVERFLOW_POLICY",
    "JOIN_COLLISION_POLICY",
    "LOCALE",
    "MAXIMUM_COLUMN_KEY_LENGTH",
    "NATIVE_SEMANTICS_VERSION",
    "NULLS_SORT_LAST_DEFAULT",
    "PIVOT_EMPTY_CELL",
    "ROUNDING_MODE",
    "ROW_ORDER_IS_INPUT_ORDER",
    "SORT_IS_STABLE",
    "STRING_COMPARISON_IS_CASE_SENSITIVE",
    "STRING_NORMALIZATION",
    "TIMEZONE",
    "semantics_fingerprint",
]
