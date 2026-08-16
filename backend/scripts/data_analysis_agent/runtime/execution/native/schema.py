"""Logical plan types mapped onto pinned Polars dtypes, in both directions.

The mapping is deliberately narrow. `PlanDataType` carries semantics the engine
does not (currency versus percentage are both decimals to Polars), so the
logical type always stays authoritative and the physical dtype is derived from
it rather than inferred from the data.
"""

from __future__ import annotations

import polars as pl

from ...models.plans import PlanColumn, PlanDataType


class NativeSchemaError(ValueError):
    """A logical column cannot be represented by the native engine."""


# Currency and percentage are stored as Float64 with the logical type retained
# in the plan schema. Fixed-scale decimal storage is a 9.5 concern and lands
# with the money-specific operations, not with this five-operation subset.
_PLAN_TO_POLARS: dict[PlanDataType, pl.DataType] = {
    PlanDataType.STRING: pl.String,
    PlanDataType.INTEGER: pl.Int64,
    PlanDataType.NUMBER: pl.Float64,
    PlanDataType.DECIMAL: pl.Float64,
    PlanDataType.CURRENCY: pl.Float64,
    PlanDataType.PERCENTAGE: pl.Float64,
    PlanDataType.BOOLEAN: pl.Boolean,
    PlanDataType.DATE: pl.Date,
    # A period ("Q1 2026", "FY2025") is a normalized label, not a calendar date.
    PlanDataType.PERIOD: pl.String,
}


def polars_dtype(data_type: PlanDataType) -> pl.DataType:
    """Return the physical dtype backing a logical plan type."""

    try:
        return _PLAN_TO_POLARS[data_type]
    except KeyError:
        raise NativeSchemaError(
            f"logical type '{data_type.value}' has no native representation"
        ) from None


def is_supported(data_type: PlanDataType) -> bool:
    return data_type in _PLAN_TO_POLARS


def frame_schema(columns: tuple[PlanColumn, ...]) -> dict[str, pl.DataType]:
    """Return the declared physical schema for a logical column tuple."""

    return {column.key: polars_dtype(column.data_type) for column in columns}


def assert_frame_matches(
    frame: pl.DataFrame,
    columns: tuple[PlanColumn, ...],
) -> None:
    """Raise when a materialized frame does not match its declared schema.

    Checked after every stage so a compiler bug surfaces as a typed error here
    instead of as a wrong answer in a published result.
    """

    expected = frame_schema(columns)
    actual = dict(frame.schema)
    if list(actual) != list(expected):
        raise NativeSchemaError(
            "native output columns do not match the declared schema: "
            f"expected {list(expected)}, produced {list(actual)}"
        )
    for key, dtype in expected.items():
        if actual[key] != dtype:
            raise NativeSchemaError(
                f"column '{key}' produced {actual[key]} instead of {dtype}"
            )


__all__ = [
    "NativeSchemaError",
    "assert_frame_matches",
    "frame_schema",
    "is_supported",
    "polars_dtype",
]
