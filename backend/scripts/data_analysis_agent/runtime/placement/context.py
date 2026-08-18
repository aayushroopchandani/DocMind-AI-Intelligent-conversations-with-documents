"""The live workbook context the browser hands back (Phase 9.11.1).

The backend computes a result and knows exactly how big it is. It does not know
what is currently sitting where that result would go, because the workbook lives
in the browser. So it asks, and this module is the shape of the answer.

Two rules govern everything here:

*An uncaptured rectangle is not an empty rectangle.* The backend never assumes
a target is free because nobody mentioned it. A rectangle is provably blank only
when the sheet's own used-range metadata places it outside all content — which
is metadata the client captured and hashed, not a guess.

*The context is hashed as a whole.* Placement is chosen against one specific
view of the workbook, and the patch it produces carries guards derived from that
same view. Binding the two through `context_hash` means a context that was
edited in flight cannot silently select a different target.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models.workbook import (
    MAX_WORKBOOK_COLUMNS,
    MAX_WORKBOOK_ROWS,
    Rect,
    a1_dimensions,
)
from ..patches.cells import CellState, canonical_cell_payload, range_hash


CONTEXT_SCHEMA_VERSION = "1.0"

MAX_CAPTURED_RANGES = 32
"""Candidate rectangles the client may offer for one placement decision."""

MAX_CAPTURED_CELLS = 50_000
"""Total cells across every capture in one context.

Deliberately far below the 250,000-cell plan output limit: a client only needs
to capture rectangles that actually overlap existing content, and a request that
wants to send a quarter of a million cells has misunderstood the handshake.
"""

MAX_STRUCTURE_RANGES = 512
"""Merges, tables, protected ranges and drawings per sheet."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CapturedRange(BaseModel):
    """One rectangle of live cells, exactly as the client read it."""

    worksheet_id: str = Field(min_length=1, max_length=200)
    range_a1: str = Field(min_length=5, max_length=100)
    cells: tuple[tuple[CellState, ...], ...] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        rows, columns = a1_dimensions(self.range_a1)
        if len(self.cells) != rows or any(
            len(row) != columns for row in self.cells
        ):
            raise ValueError(
                f"captured cells do not match {self.range_a1} "
                f"({rows}x{columns})"
            )
        if rows * columns > MAX_CAPTURED_CELLS:
            raise ValueError(
                f"a single capture may not exceed {MAX_CAPTURED_CELLS} cells"
            )
        return self

    @property
    def rect(self) -> Rect:
        return Rect.from_a1(self.range_a1)

    @property
    def cell_count(self) -> int:
        return len(self.cells) * len(self.cells[0])

    @property
    def content_hash(self) -> str:
        return range_hash(self.range_a1, self.cells)

    @property
    def is_blank(self) -> bool:
        return all(cell.is_blank for row in self.cells for cell in row)

    def covers(self, rect: Rect, *, worksheet_id: str) -> bool:
        return worksheet_id == self.worksheet_id and self.rect.contains(rect)


class SheetOccupancy(BaseModel):
    """What one worksheet contains, in rectangles rather than cells.

    This is the metadata that lets placement answer "is this area free?" without
    reading a whole sheet. `used_range_a1` is the decisive one: a target outside
    it is provably empty, so neither side has to move the cells to prove it.
    """

    worksheet_id: str = Field(min_length=1, max_length=200)
    worksheet_name: str = Field(min_length=1, max_length=255)
    row_count: int = Field(ge=1, le=MAX_WORKBOOK_ROWS)
    column_count: int = Field(ge=1, le=MAX_WORKBOOK_COLUMNS)
    used_range_a1: str | None = Field(default=None, max_length=100)
    merged_ranges: tuple[str, ...] = Field(default=(), max_length=MAX_STRUCTURE_RANGES)
    protected_ranges: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_STRUCTURE_RANGES,
    )
    table_ranges: tuple[str, ...] = Field(default=(), max_length=MAX_STRUCTURE_RANGES)
    drawing_ranges: tuple[str, ...] = Field(default=(), max_length=MAX_STRUCTURE_RANGES)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @field_validator(
        "merged_ranges",
        "protected_ranges",
        "table_ranges",
        "drawing_ranges",
        mode="before",
    )
    @classmethod
    def normalize_structures(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("structure ranges must be a list or tuple")
        # Deduplicated at the boundary: a client that reports the same merge
        # twice should not double the collision work.
        return tuple(dict.fromkeys(str(item).strip() for item in value if item))

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        for group in (
            self.merged_ranges,
            self.protected_ranges,
            self.table_ranges,
            self.drawing_ranges,
        ):
            for item in group:
                a1_dimensions(item)
        if self.used_range_a1 is not None:
            a1_dimensions(self.used_range_a1)
        return self

    @property
    def used_rect(self) -> Rect | None:
        if self.used_range_a1 is None:
            return None
        return Rect.from_a1(self.used_range_a1)

    @property
    def limit(self) -> Rect:
        return Rect(
            first_row=1,
            first_column=1,
            last_row=self.row_count,
            last_column=self.column_count,
        )


class WorkbookPatchContext(BaseModel):
    """One hashed, bounded view of the live workbook at a known revision."""

    context_schema_version: str = CONTEXT_SCHEMA_VERSION
    workbook_id: str = Field(min_length=1, max_length=200)
    workbook_revision: int = Field(ge=0)
    sheets: tuple[SheetOccupancy, ...] = Field(min_length=1, max_length=200)
    source: CapturedRange | None = None
    candidates: tuple[CapturedRange, ...] = Field(
        default=(),
        max_length=MAX_CAPTURED_RANGES,
    )
    idempotency_key: str = Field(min_length=8, max_length=200)
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        sheet_ids = [sheet.worksheet_id for sheet in self.sheets]
        if len(sheet_ids) != len(set(sheet_ids)):
            raise ValueError("worksheet IDs must be unique")
        names = [sheet.worksheet_name.casefold() for sheet in self.sheets]
        if len(names) != len(set(names)):
            raise ValueError("worksheet names must be unique")
        known = set(sheet_ids)
        captures = (
            (self.source, *self.candidates)
            if self.source is not None
            else self.candidates
        )
        total_cells = 0
        for capture in captures:
            if capture.worksheet_id not in known:
                raise ValueError(
                    f"capture references unknown worksheet "
                    f"'{capture.worksheet_id}'"
                )
            total_cells += capture.cell_count
        if total_cells > MAX_CAPTURED_CELLS:
            raise ValueError(
                f"captured context exceeds {MAX_CAPTURED_CELLS} cells"
            )
        if self.context_hash != compute_context_hash(self):
            raise ValueError("context_hash does not match the captured context")
        return self

    def sheet(self, worksheet_id: str) -> SheetOccupancy | None:
        for sheet in self.sheets:
            if sheet.worksheet_id == worksheet_id:
                return sheet
        return None

    def sheet_by_name(self, worksheet_name: str) -> SheetOccupancy | None:
        folded = worksheet_name.casefold()
        for sheet in self.sheets:
            if sheet.worksheet_name.casefold() == folded:
                return sheet
        return None

    def capture_for(
        self,
        rect: Rect,
        *,
        worksheet_id: str,
    ) -> CapturedRange | None:
        """Return the capture containing `rect`, preferring an exact match."""

        containing: CapturedRange | None = None
        for capture in self.candidates:
            if not capture.covers(rect, worksheet_id=worksheet_id):
                continue
            if capture.rect == rect:
                return capture
            if containing is None:
                containing = capture
        return containing

    @property
    def worksheet_names(self) -> tuple[str, ...]:
        return tuple(sheet.worksheet_name for sheet in self.sheets)


def canonical_context_payload(context: WorkbookPatchContext) -> dict[str, Any]:
    """Return exactly what the context hash commits to."""

    return {
        "context_schema_version": context.context_schema_version,
        "workbook_id": context.workbook_id,
        "workbook_revision": context.workbook_revision,
        "idempotency_key": context.idempotency_key,
        "sheets": [
            {
                "worksheet_id": sheet.worksheet_id,
                "worksheet_name": sheet.worksheet_name,
                "row_count": sheet.row_count,
                "column_count": sheet.column_count,
                "used_range_a1": sheet.used_range_a1,
                "merged_ranges": list(sheet.merged_ranges),
                "protected_ranges": list(sheet.protected_ranges),
                "table_ranges": list(sheet.table_ranges),
                "drawing_ranges": list(sheet.drawing_ranges),
            }
            for sheet in context.sheets
        ],
        "source": _capture_payload(context.source),
        "candidates": [_capture_payload(item) for item in context.candidates],
    }


def compute_context_hash(context: WorkbookPatchContext) -> str:
    """Return the canonical hash for `context`."""

    encoded = json.dumps(
        canonical_context_payload(context),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _capture_payload(capture: CapturedRange | None) -> dict[str, Any] | None:
    if capture is None:
        return None
    return {
        "worksheet_id": capture.worksheet_id,
        "range_a1": capture.range_a1,
        "cells": [
            [canonical_cell_payload(cell) for cell in row] for row in capture.cells
        ],
    }


__all__ = [
    "CONTEXT_SCHEMA_VERSION",
    "MAX_CAPTURED_CELLS",
    "MAX_CAPTURED_RANGES",
    "MAX_STRUCTURE_RANGES",
    "CapturedRange",
    "SheetOccupancy",
    "WorkbookPatchContext",
    "canonical_context_payload",
    "compute_context_hash",
]
