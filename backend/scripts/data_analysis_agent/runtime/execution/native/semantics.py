"""The pinned semantic policy every native execution runs under.

Phase 9.5 defines exact semantics for every operation. Phase 9.4 only ships five
operations, so only the rules those five depend on are fixed here. The version
below covers that subset; widening the operation set must bump it, because a
semantics change has to produce a new execution key (9.5 acceptance criteria).

Nothing here may rely on an engine default. Polars' own defaults for null
ordering, sort stability and cast strictness are all explicitly overridden by the
compilers so that an engine upgrade cannot silently change a result.
"""

from __future__ import annotations

from typing import Final


NATIVE_SEMANTICS_VERSION: Final = "1.0"
"""Covers: filter_rows, select_columns, sort_rows, aggregate, derive_column."""

TIMEZONE: Final = "UTC"
LOCALE: Final = "en-US"

DECIMAL_SCALE: Final = 6
"""Default scale for a derived numeric value with no declared rounding."""

ROUNDING_MODE: Final = "half_even"
"""Banker's rounding, so repeated aggregation does not drift upward."""

NULL_PREDICATE_RESULT: Final = False
"""A predicate that evaluates to null excludes the row unless the plan says
otherwise. Matches 9.5's "treat a null predicate result as false"."""

SORT_IS_STABLE: Final = True
NULLS_SORT_LAST_DEFAULT: Final = True

STRING_COMPARISON_IS_CASE_SENSITIVE: Final = True
EMPTY_STRING_IS_NOT_NULL: Final = True
"""Phase 7 normalization already decided which markers mean "missing"; the
engine never re-derives them, so an empty string stays an empty string."""

ALLOW_NON_FINITE_FLOATS: Final = False
"""NaN and infinity never reach a published result; they fail validation."""

ROW_ORDINAL_COLUMN: Final = "__native_row_ordinal__"
"""Hidden ascending ordinal added to every input so that ties, keep-first and
keep-last are decided by input order rather than by engine scheduling."""


def semantics_fingerprint() -> dict[str, object]:
    """Return the policy as canonical content for the execution key."""

    return {
        "native_semantics_version": NATIVE_SEMANTICS_VERSION,
        "timezone": TIMEZONE,
        "locale": LOCALE,
        "decimal_scale": DECIMAL_SCALE,
        "rounding_mode": ROUNDING_MODE,
        "null_predicate_result": NULL_PREDICATE_RESULT,
        "sort_is_stable": SORT_IS_STABLE,
        "nulls_sort_last_default": NULLS_SORT_LAST_DEFAULT,
        "string_comparison_is_case_sensitive": STRING_COMPARISON_IS_CASE_SENSITIVE,
        "empty_string_is_not_null": EMPTY_STRING_IS_NOT_NULL,
        "allow_non_finite_floats": ALLOW_NON_FINITE_FLOATS,
    }


__all__ = [
    "ALLOW_NON_FINITE_FLOATS",
    "DECIMAL_SCALE",
    "EMPTY_STRING_IS_NOT_NULL",
    "LOCALE",
    "NATIVE_SEMANTICS_VERSION",
    "NULLS_SORT_LAST_DEFAULT",
    "NULL_PREDICATE_RESULT",
    "ROUNDING_MODE",
    "ROW_ORDINAL_COLUMN",
    "SORT_IS_STABLE",
    "STRING_COMPARISON_IS_CASE_SENSITIVE",
    "TIMEZONE",
    "semantics_fingerprint",
]
