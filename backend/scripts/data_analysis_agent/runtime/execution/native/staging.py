"""Write resolved input rows to Arrow IPC for the engine to read.

Staging happens in the parent, where the rows already are, so the child process
only ever receives file paths. Types come from the plan's declared schema rather
than from inference, which is what keeps a column of all-null values, or a
column of integers that happens to look like a date, from changing shape between
one run and its replay.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

import polars as pl

from ...models.plans import PlanColumn, PlanDataType
from .schema import frame_schema


class StagingError(ValueError):
    """Resolved rows cannot be represented under the declared schema."""


def build_frame(
    columns: tuple[PlanColumn, ...],
    rows: Sequence[dict[str, Any]],
) -> pl.DataFrame:
    """Return a typed frame for `rows` under the declared logical schema."""

    schema = frame_schema(columns)
    data = {
        column.key: [_coerce(row.get(column.key), column) for row in rows]
        for column in columns
    }
    try:
        return pl.DataFrame(data, schema=schema, strict=False)
    except (pl.exceptions.PolarsError, TypeError, ValueError) as exc:
        raise StagingError(
            f"input rows do not fit the declared schema: {exc}"
        ) from exc


def write_ipc(
    columns: tuple[PlanColumn, ...],
    rows: Sequence[dict[str, Any]],
    *,
    path: Path,
) -> int:
    """Stage one input table and return its size in bytes."""

    frame = build_frame(columns, rows)
    frame.write_ipc(path, compression="zstd")
    return path.stat().st_size


def _coerce(value: Any, column: PlanColumn) -> Any:
    """Normalize a stored JSON value into its declared logical type."""

    if value is None or value == "":
        # Phase 7 already decided which markers mean "missing"; an empty string
        # surviving to here is an empty cell, not a sentinel to reinterpret.
        return None if value is None else _empty(column)
    if column.data_type == PlanDataType.DATE:
        return _as_date(value)
    if column.data_type == PlanDataType.BOOLEAN:
        return value if isinstance(value, bool) else None
    if column.data_type == PlanDataType.INTEGER:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return None
    if column.data_type in _NUMERIC:
        if isinstance(value, bool):
            return None
        return float(value) if isinstance(value, (int, float)) else None
    return value if isinstance(value, str) else str(value)


_NUMERIC = frozenset(
    {
        PlanDataType.NUMBER,
        PlanDataType.DECIMAL,
        PlanDataType.CURRENCY,
        PlanDataType.PERCENTAGE,
    }
)


def _empty(column: PlanColumn) -> Any:
    # An empty string is a real value for text columns and missing for others.
    return "" if column.data_type in {PlanDataType.STRING, PlanDataType.PERIOD} else None


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


__all__ = ["StagingError", "build_frame", "write_ipc"]
