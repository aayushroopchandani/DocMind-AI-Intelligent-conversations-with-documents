"""Turning a computed result into cells (Phase 9.11 → 9.10 hand-off).

The execution engine produces typed columns and rows. A spreadsheet holds cells.
This is the one place that translation happens, and it makes three decisions
that matter more than they look:

*A null is a blank cell, not a typed empty one.* Blank has a canonical hash, and
a null in a number column must produce exactly that hash — otherwise a target
rectangle would hash differently depending on which columns happened to be
empty.

*Text that looks like a formula is written as text.* A result value beginning
with `=`, `+`, `-` or `@` becomes live formula the moment the workbook opens.
The Phase 9.7 neutralizer already knows the portable fix, so it is applied to
every string that reaches a cell, headers included.

*Formats come from the declared column type, not from the values.* Currency
formats as currency even when the first row happens to be a whole number.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from ..formulas.safety import neutralize_text
from ..models.plans import PlanColumn, PlanDataType
from ..models.workbook import WorkbookCellType
from .cells import BLANK_CELL, CellState


MAX_CELL_TEXT = 4_096
"""Longer values are truncated; a spreadsheet cell is not a document store."""


_CELL_TYPES: dict[PlanDataType, WorkbookCellType] = {
    PlanDataType.STRING: WorkbookCellType.STRING,
    PlanDataType.INTEGER: WorkbookCellType.NUMBER,
    PlanDataType.NUMBER: WorkbookCellType.NUMBER,
    PlanDataType.DECIMAL: WorkbookCellType.NUMBER,
    PlanDataType.CURRENCY: WorkbookCellType.NUMBER,
    PlanDataType.PERCENTAGE: WorkbookCellType.NUMBER,
    PlanDataType.BOOLEAN: WorkbookCellType.BOOLEAN,
    PlanDataType.DATE: WorkbookCellType.DATE,
    PlanDataType.PERIOD: WorkbookCellType.STRING,
    PlanDataType.UNKNOWN: WorkbookCellType.STRING,
}

_NUMBER_FORMATS: dict[PlanDataType, str] = {
    PlanDataType.INTEGER: "#,##0",
    PlanDataType.DECIMAL: "#,##0.00",
    PlanDataType.CURRENCY: "#,##0.00",
    PlanDataType.PERCENTAGE: "0.00%",
    PlanDataType.DATE: "yyyy-mm-dd",
}


def header_cell(column: PlanColumn) -> CellState:
    return CellState(
        value=neutralize_text(column.label[:MAX_CELL_TEXT]),
        cell_type=WorkbookCellType.STRING,
    )


def value_cell(value: object, column: PlanColumn) -> CellState:
    """Return the cell one result value becomes."""

    if value is None:
        return BLANK_CELL
    number_format = _NUMBER_FORMATS.get(column.data_type)
    cell_type = _CELL_TYPES.get(column.data_type, WorkbookCellType.STRING)
    if isinstance(value, bool):
        return CellState(value=value, cell_type=WorkbookCellType.BOOLEAN)
    if isinstance(value, (datetime, date)):
        return CellState(
            value=value.isoformat(),
            cell_type=WorkbookCellType.DATE,
            number_format=number_format or "yyyy-mm-dd",
        )
    if isinstance(value, Decimal):
        return CellState(
            value=float(value),
            cell_type=WorkbookCellType.NUMBER,
            number_format=number_format,
        )
    if isinstance(value, (int, float)):
        return CellState(
            value=value,
            cell_type=cell_type,
            number_format=number_format,
        )
    return CellState(
        value=neutralize_text(str(value)[:MAX_CELL_TEXT]),
        cell_type=WorkbookCellType.STRING,
    )


@dataclass(frozen=True, slots=True)
class ResultGrid:
    """A result as rows of cells, streamed rather than materialized.

    `records` is consumed exactly once. Nothing here holds more than a single
    row, so a 250,000-cell result costs one row of memory rather than three
    full copies of itself.
    """

    columns: tuple[PlanColumn, ...]
    records: Iterable[Sequence[object]]
    record_count: int
    include_header: bool = True

    def __post_init__(self) -> None:
        if not self.columns:
            raise ValueError("a result grid needs at least one column")
        if self.record_count < 0:
            raise ValueError("record_count cannot be negative")
        if self.rows < 1:
            raise ValueError("a result grid needs at least one row")

    @property
    def width(self) -> int:
        return len(self.columns)

    @property
    def rows(self) -> int:
        return self.record_count + (1 if self.include_header else 0)

    @property
    def cell_count(self) -> int:
        return self.rows * self.width

    def __iter__(self) -> Iterator[tuple[CellState, ...]]:
        if self.include_header:
            yield tuple(header_cell(column) for column in self.columns)
        emitted = 0
        for record in self.records:
            if len(record) != self.width:
                raise ValueError(
                    f"result row has {len(record)} values; the grid is "
                    f"{self.width} columns wide"
                )
            emitted += 1
            if emitted > self.record_count:
                raise ValueError(
                    f"result produced more than the declared "
                    f"{self.record_count} rows"
                )
            yield tuple(
                value_cell(value, column)
                for value, column in zip(record, self.columns)
            )
        if emitted != self.record_count:
            raise ValueError(
                f"result produced {emitted} rows; {self.record_count} were "
                "declared"
            )


@dataclass(frozen=True, slots=True)
class MaterializedGrid:
    """Cells that already exist — captured previous state, for an inverse."""

    cells: tuple[tuple[CellState, ...], ...]

    def __post_init__(self) -> None:
        if not self.cells or not self.cells[0]:
            raise ValueError("a materialized grid needs at least one cell")
        if any(len(row) != len(self.cells[0]) for row in self.cells):
            raise ValueError("a materialized grid must be rectangular")

    @property
    def width(self) -> int:
        return len(self.cells[0])

    @property
    def rows(self) -> int:
        return len(self.cells)

    @property
    def cell_count(self) -> int:
        return self.rows * self.width

    def __iter__(self) -> Iterator[tuple[CellState, ...]]:
        return iter(self.cells)


__all__ = [
    "MAX_CELL_TEXT",
    "MaterializedGrid",
    "ResultGrid",
    "header_cell",
    "value_cell",
]
