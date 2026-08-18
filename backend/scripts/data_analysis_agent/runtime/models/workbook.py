from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


_A1_RE = re.compile(
    r"^(?:(?:'(?P<quoted>(?:[^']|'')+)'|(?P<plain>[^'!]+))!)?"
    r"(?P<start_col>[A-Z]{1,3})(?P<start_row>[1-9][0-9]*)"
    r"(?::(?P<end_col>[A-Z]{1,3})(?P<end_row>[1-9][0-9]*))?$",
    re.IGNORECASE,
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"
MAX_WORKBOOK_ROWS = 1_048_576
MAX_WORKBOOK_COLUMNS = 16_384

CellValue = str | int | float | bool | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkbookCellType(str, Enum):
    BLANK = "blank"
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    FORMULA = "formula"
    ERROR = "error"


def _column_number(label: str) -> int:
    value = 0
    for character in label.upper():
        value = value * 26 + (ord(character) - ord("A") + 1)
    return value


@dataclass(frozen=True, slots=True)
class _A1Range:
    sheet_name: str | None
    start_column: int
    start_row: int
    end_column: int
    end_row: int

    @property
    def dimensions(self) -> tuple[int, int]:
        return (
            self.end_row - self.start_row + 1,
            self.end_column - self.start_column + 1,
        )

    def has_same_cells(self, other: _A1Range) -> bool:
        return (
            self.start_column,
            self.start_row,
            self.end_column,
            self.end_row,
        ) == (
            other.start_column,
            other.start_row,
            other.end_column,
            other.end_row,
        )

    def contains(self, other: _A1Range) -> bool:
        return (
            self.start_column <= other.start_column
            and self.start_row <= other.start_row
            and self.end_column >= other.end_column
            and self.end_row >= other.end_row
        )


def _parse_a1_range(value: str) -> _A1Range:
    match = _A1_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError("range must be a bounded A1 range")
    start_column = _column_number(match.group("start_col"))
    end_column = _column_number(match.group("end_col") or match.group("start_col"))
    start_row = int(match.group("start_row"))
    end_row = int(match.group("end_row") or match.group("start_row"))
    if end_column < start_column or end_row < start_row:
        raise ValueError("A1 range end cannot precede its start")
    if end_column > MAX_WORKBOOK_COLUMNS or end_row > MAX_WORKBOOK_ROWS:
        raise ValueError("A1 range exceeds spreadsheet limits")
    quoted_sheet = match.group("quoted")
    sheet_name = (
        quoted_sheet.replace("''", "'")
        if quoted_sheet is not None
        else match.group("plain")
    )
    return _A1Range(
        sheet_name=sheet_name,
        start_column=start_column,
        start_row=start_row,
        end_column=end_column,
        end_row=end_row,
    )


def a1_dimensions(value: str) -> tuple[int, int]:
    return _parse_a1_range(value).dimensions


def a1_bounds(value: str) -> tuple[int, int, int, int]:
    """Return `(first_row, first_column, last_row, last_column)`, 1-based.

    The one public way to turn an A1 range into arithmetic. Placement,
    reservations and collision checks all need rectangle coordinates, and every
    one of them must agree with the parser the guards and snapshots already use
    — so they read bounds from here rather than parsing A1 a second time.
    """

    parsed = _parse_a1_range(value)
    return (
        parsed.start_row,
        parsed.start_column,
        parsed.end_row,
        parsed.end_column,
    )


def a1_sheet_name(value: str) -> str | None:
    """Return the sheet qualifier of `value`, if it carries one."""

    return _parse_a1_range(value).sheet_name


def a1_from_bounds(
    first_row: int,
    first_column: int,
    last_row: int,
    last_column: int,
    *,
    sheet_name: str | None = None,
) -> str:
    """Return the absolute A1 range covering the given 1-based bounds."""

    if first_row < 1 or first_column < 1:
        raise ValueError("A1 bounds are 1-based")
    if last_row < first_row or last_column < first_column:
        raise ValueError("A1 range end cannot precede its start")
    if last_row > MAX_WORKBOOK_ROWS or last_column > MAX_WORKBOOK_COLUMNS:
        raise ValueError("A1 range exceeds spreadsheet limits")
    prefix = ""
    if sheet_name is not None:
        escaped = sheet_name.replace("'", "''")
        prefix = f"'{escaped}'!"
    return (
        f"{prefix}{_column_label(first_column)}{first_row}:"
        f"{_column_label(last_column)}{last_row}"
    )


def a1_ranges_overlap(left: str, right: str) -> bool:
    """Return whether two bounded ranges on the same sheet share any cell."""

    left_range = _parse_a1_range(left)
    right_range = _parse_a1_range(right)
    if (
        left_range.sheet_name is not None
        and right_range.sheet_name is not None
        and left_range.sheet_name.casefold() != right_range.sheet_name.casefold()
    ):
        return False
    return not (
        left_range.end_column < right_range.start_column
        or right_range.end_column < left_range.start_column
        or left_range.end_row < right_range.start_row
        or right_range.end_row < left_range.start_row
    )


def a1_subrange(
    value: str,
    *,
    row_offset: int,
    column_offset: int,
    row_count: int,
    column_count: int,
) -> str:
    """Return an absolute A1 rectangle inside `value` using zero-based offsets."""

    if min(row_offset, column_offset) < 0:
        raise ValueError("subrange offsets must be non-negative")
    if min(row_count, column_count) <= 0:
        raise ValueError("subrange dimensions must be positive")
    source = _parse_a1_range(value)
    start_row = source.start_row + row_offset
    start_column = source.start_column + column_offset
    end_row = start_row + row_count - 1
    end_column = start_column + column_count - 1
    if end_row > source.end_row or end_column > source.end_column:
        raise ValueError("subrange must be contained in the source range")
    sheet = ""
    if source.sheet_name is not None:
        escaped = source.sheet_name.replace("'", "''")
        sheet = f"'{escaped}'!"
    return (
        f"{sheet}{_column_label(start_column)}{start_row}:"
        f"{_column_label(end_column)}{end_row}"
    )


def column_label(number: int) -> str:
    """Return the spreadsheet letter for a 1-based column number."""

    return _column_label(number)


def _column_label(number: int) -> str:
    if not 1 <= number <= MAX_WORKBOOK_COLUMNS:
        raise ValueError("column number exceeds spreadsheet limits")
    output: list[str] = []
    value = number
    while value:
        value, remainder = divmod(value - 1, 26)
        output.append(chr(ord("A") + remainder))
    return "".join(reversed(output))


@dataclass(frozen=True, slots=True)
class Rect:
    """A bounded, 1-based rectangle of cells on one worksheet.

    Placement, collision checks and write reservations are all interval
    arithmetic, and doing that arithmetic on A1 strings is how off-by-one
    overwrites happen. Every one of them converts to this once, reasons in
    integers, and converts back only to address a range.

    The sheet is deliberately *not* part of the rectangle: two rectangles are
    only comparable once the caller has established they are on the same sheet,
    and folding that into `intersects` would make it easy to forget.
    """

    first_row: int
    first_column: int
    last_row: int
    last_column: int

    def __post_init__(self) -> None:
        if self.first_row < 1 or self.first_column < 1:
            raise ValueError("rectangles are 1-based")
        if self.last_row < self.first_row or self.last_column < self.first_column:
            raise ValueError("rectangle end cannot precede its start")

    @classmethod
    def from_a1(cls, value: str) -> Rect:
        first_row, first_column, last_row, last_column = a1_bounds(value)
        return cls(
            first_row=first_row,
            first_column=first_column,
            last_row=last_row,
            last_column=last_column,
        )

    @classmethod
    def sized(cls, *, first_row: int, first_column: int, rows: int, columns: int) -> Rect:
        if rows < 1 or columns < 1:
            raise ValueError("rectangle dimensions must be positive")
        return cls(
            first_row=first_row,
            first_column=first_column,
            last_row=first_row + rows - 1,
            last_column=first_column + columns - 1,
        )

    def to_a1(self, *, sheet_name: str | None = None) -> str:
        return a1_from_bounds(
            self.first_row,
            self.first_column,
            self.last_row,
            self.last_column,
            sheet_name=sheet_name,
        )

    @property
    def rows(self) -> int:
        return self.last_row - self.first_row + 1

    @property
    def columns(self) -> int:
        return self.last_column - self.first_column + 1

    @property
    def cell_count(self) -> int:
        return self.rows * self.columns

    @property
    def within_sheet_limits(self) -> bool:
        return (
            self.last_row <= MAX_WORKBOOK_ROWS
            and self.last_column <= MAX_WORKBOOK_COLUMNS
        )

    def intersects(self, other: Rect) -> bool:
        return not (
            self.last_column < other.first_column
            or other.last_column < self.first_column
            or self.last_row < other.first_row
            or other.last_row < self.first_row
        )

    def contains(self, other: Rect) -> bool:
        return (
            self.first_row <= other.first_row
            and self.first_column <= other.first_column
            and self.last_row >= other.last_row
            and self.last_column >= other.last_column
        )


class WorkbookRangeSnapshot(BaseModel):
    """Bounded analytical cell data captured from the live workbook engine."""

    range_a1: str = Field(min_length=5, max_length=100)
    values: tuple[tuple[CellValue, ...], ...]
    formulas: tuple[tuple[str | None, ...], ...]
    cell_types: tuple[tuple[WorkbookCellType | None, ...], ...]
    number_formats: tuple[tuple[str | None, ...], ...]
    column_headers: tuple[str, ...] = ()
    header_row_index: int | None = Field(default=None, ge=0)
    row_count: int = Field(ge=1, le=MAX_WORKBOOK_ROWS)
    column_count: int = Field(ge=1, le=MAX_WORKBOOK_COLUMNS)
    merged_ranges: tuple[str, ...] = Field(default=(), max_length=10_000)
    hidden_rows: tuple[int, ...] = Field(default=(), max_length=MAX_WORKBOOK_ROWS)
    hidden_columns: tuple[int, ...] = Field(
        default=(),
        max_length=MAX_WORKBOOK_COLUMNS,
    )

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )

    @field_validator("range_a1", "merged_ranges", mode="before")
    @classmethod
    def normalize_ranges(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(str(item or "").strip() for item in value)
        return str(value or "").strip()

    @field_validator("hidden_rows", "hidden_columns", mode="before")
    @classmethod
    def deduplicate_indexes(cls, value: object) -> tuple[int, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("hidden indexes must be a list or tuple")
        return tuple(sorted({int(item) for item in value}))

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        a1_rows, a1_columns = a1_dimensions(self.range_a1)
        if (a1_rows, a1_columns) != (self.row_count, self.column_count):
            raise ValueError("A1 dimensions must match row_count and column_count")
        matrices = {
            "values": self.values,
            "formulas": self.formulas,
            "cell_types": self.cell_types,
            "number_formats": self.number_formats,
        }
        for name, matrix in matrices.items():
            if len(matrix) != self.row_count:
                raise ValueError(f"{name} must contain row_count rows")
            if any(len(row) != self.column_count for row in matrix):
                raise ValueError(f"{name} must be rectangular")
        if self.column_headers and len(self.column_headers) != self.column_count:
            raise ValueError("column_headers must match column_count")
        if self.header_row_index is not None:
            if self.header_row_index >= self.row_count:
                raise ValueError("header_row_index is outside the snapshot")
        if any(index < 0 or index >= self.row_count for index in self.hidden_rows):
            raise ValueError("hidden row index is outside the snapshot")
        if any(
            index < 0 or index >= self.column_count
            for index in self.hidden_columns
        ):
            raise ValueError("hidden column index is outside the snapshot")
        for merged_range in self.merged_ranges:
            a1_dimensions(merged_range)
        return self

    @property
    def cell_count(self) -> int:
        return self.row_count * self.column_count


class SpreadsheetContext(BaseModel):
    workbook_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    )
    workbook_name: str = Field(min_length=1, max_length=255)
    client_revision: int = Field(ge=0)
    worksheet_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    )
    worksheet_name: str = Field(min_length=1, max_length=255)
    selected_range: str | None = Field(default=None, max_length=100)
    used_range: str = Field(min_length=5, max_length=100)
    snapshot_range: str = Field(min_length=5, max_length=100)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot: WorkbookRangeSnapshot | None = None
    snapshot_artifact_version_id: str | None = Field(
        default=None,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    )
    locale: str = Field(default="en-US", min_length=2, max_length=35)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    captured_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @field_validator(
        "selected_range",
        "used_range",
        "snapshot_range",
        mode="before",
    )
    @classmethod
    def normalize_range(cls, value: object) -> str | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        a1_dimensions(normalized)
        return normalized

    @field_validator("snapshot_hash", mode="before")
    @classmethod
    def normalize_hash(cls, value: object) -> str:
        normalized = str(value or "").strip().casefold()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("snapshot_hash must be a lowercase SHA-256 digest")
        return normalized

    @model_validator(mode="after")
    def validate_snapshot_source(self) -> Self:
        if (self.snapshot is None) == (self.snapshot_artifact_version_id is None):
            raise ValueError(
                "exactly one inline snapshot or snapshot artifact is required"
            )

        used_range = _parse_a1_range(self.used_range)
        selected_range = (
            _parse_a1_range(self.selected_range)
            if self.selected_range is not None
            else None
        )
        snapshot_range = _parse_a1_range(self.snapshot_range)
        for field_name, parsed_range in (
            ("used_range", used_range),
            ("selected_range", selected_range),
            ("snapshot_range", snapshot_range),
        ):
            if (
                parsed_range is not None
                and parsed_range.sheet_name is not None
                and parsed_range.sheet_name.casefold()
                != self.worksheet_name.casefold()
            ):
                raise ValueError(
                    f"{field_name} sheet qualifier must match worksheet_name"
                )

        if selected_range is not None and not used_range.contains(selected_range):
            raise ValueError("selected_range must be contained in used_range")

        expected_range = selected_range or used_range
        if not snapshot_range.has_same_cells(expected_range):
            source_name = (
                "selected_range"
                if selected_range is not None
                else "used_range"
            )
            raise ValueError(f"snapshot_range must match {source_name}")

        if self.snapshot is not None:
            if self.snapshot.range_a1 != self.snapshot_range:
                raise ValueError("snapshot range must match the inline snapshot")
            calculated = canonical_snapshot_hash(self.snapshot)
            if calculated != self.snapshot_hash:
                raise ValueError("snapshot_hash does not match the inline snapshot")
        return self


def _canonical_number(value: int | float) -> str:
    if isinstance(value, bool):
        raise TypeError("booleans are not numeric cells")
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        raise ValueError("workbook numbers must be finite")
    if value == 0:
        return "0"
    if value.is_integer():
        return str(int(value))
    # 17 significant digits round-trip an IEEE-754 double. The tagged string
    # avoids Python/JavaScript JSON formatting differences in cross-client hashes.
    return format(value, ".17g").replace("E", "e")


def _canonical_cell(value: CellValue) -> dict[str, JsonValue]:
    if value is None:
        return {"t": "null", "v": None}
    if isinstance(value, bool):
        return {"t": "boolean", "v": value}
    if isinstance(value, (int, float)):
        return {"t": "number", "v": _canonical_number(value)}
    return {"t": "string", "v": value}


def canonical_snapshot_payload(snapshot: WorkbookRangeSnapshot) -> dict[str, JsonValue]:
    """Canonical representation shared with the future browser hash helper."""

    return {
        "schema_version": 1,
        "range": snapshot.range_a1,
        "rows": snapshot.row_count,
        "columns": snapshot.column_count,
        "values": [
            [_canonical_cell(value) for value in row] for row in snapshot.values
        ],
        "formulas": [list(row) for row in snapshot.formulas],
        "cell_types": [
            [item.value if item is not None else None for item in row]
            for row in snapshot.cell_types
        ],
        "number_formats": [list(row) for row in snapshot.number_formats],
        "column_headers": list(snapshot.column_headers),
        "header_row_index": snapshot.header_row_index,
        "merged_ranges": list(snapshot.merged_ranges),
        "hidden_rows": list(snapshot.hidden_rows),
        "hidden_columns": list(snapshot.hidden_columns),
    }


def canonical_snapshot_bytes(snapshot: WorkbookRangeSnapshot) -> bytes:
    return json.dumps(
        canonical_snapshot_payload(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_snapshot_hash(snapshot: WorkbookRangeSnapshot) -> str:
    return hashlib.sha256(canonical_snapshot_bytes(snapshot)).hexdigest()


__all__ = [
    "CellValue",
    "MAX_WORKBOOK_COLUMNS",
    "MAX_WORKBOOK_ROWS",
    "Rect",
    "SpreadsheetContext",
    "WorkbookCellType",
    "WorkbookRangeSnapshot",
    "a1_bounds",
    "a1_dimensions",
    "a1_from_bounds",
    "a1_ranges_overlap",
    "a1_sheet_name",
    "a1_subrange",
    "canonical_snapshot_bytes",
    "canonical_snapshot_hash",
    "canonical_snapshot_payload",
    "column_label",
]
