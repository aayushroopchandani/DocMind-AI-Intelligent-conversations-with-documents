"""The canonical cell hash (Phase 9.10.4).

One algorithm, implemented twice — here and in
`frontend/my-app/lib/data-analysis/patch/cell-hash.ts` — and pinned by golden
fixtures both sides run. If the two ever disagree, a patch that the backend
compiled against one view of the sheet would be applied against another, which
is exactly the silent corruption the guards exist to prevent.

Two decisions worth stating, because both are places where "obvious" is wrong:

*A blank cell has a hash.* A missing cell and an explicitly blank cell must
produce the same digest, or a rectangle would hash differently depending on how
the client happened to enumerate it.

*Only what affects safe application is covered.* Value, formula, type, number
format, and merged/protected state. Fill colour and font are excluded on
purpose: a patch that refuses to apply because someone changed a cell's
background would be useless, and colour cannot make an overwrite unsafe.

The number canonicalization is shared with `models/workbook.py`, which the
snapshot hash already uses, so a cell hashes identically whether it arrives
through a snapshot or a patch guard.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models.workbook import (
    CellValue,
    WorkbookCellType,
    _canonical_cell,
    a1_dimensions,
)


CELL_HASH_VERSION = "1.0"
"""Part of every digest, so a future change to the algorithm cannot be mistaken
for a change to the data."""


class CellState(BaseModel):
    """Everything about one cell that affects whether a write is safe."""

    value: CellValue = None
    formula: str | None = Field(default=None, max_length=8_192)
    cell_type: WorkbookCellType | None = None
    number_format: str | None = Field(default=None, max_length=120)
    merged: bool = False
    protected: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)

    @property
    def is_blank(self) -> bool:
        """Whether this cell is empty for collision purposes.

        Formatting alone does not make a cell occupied — a target rectangle
        that is merely styled is still safe to write into.
        """

        return (
            self.value is None
            and not self.formula
            and not self.merged
            and not self.protected
        )


BLANK_CELL = CellState()


def canonical_cell_payload(cell: CellState) -> dict[str, Any]:
    """Return the canonical form both languages serialize."""

    return {
        "v": _canonical_cell(cell.value),
        "f": cell.formula or None,
        "t": cell.cell_type.value if cell.cell_type is not None else None,
        "n": cell.number_format or None,
        "m": cell.merged,
        "p": cell.protected,
    }


def cell_hash(cell: CellState) -> str:
    """Return the digest for one cell."""

    return _sha256(
        {
            "schema_version": CELL_HASH_VERSION,
            "cell": canonical_cell_payload(cell),
        }
    )


def range_hash(
    range_a1: str,
    cells: tuple[tuple[CellState, ...], ...],
) -> str:
    """Return the digest for a rectangle of cells.

    The range is part of the digest, so the same values at a different address
    hash differently — a guard must not pass because the right content happened
    to exist somewhere else.
    """

    rows, columns = a1_dimensions(range_a1)
    if len(cells) != rows or any(len(row) != columns for row in cells):
        raise ValueError(
            f"cell grid does not match {range_a1} ({rows}x{columns})"
        )
    return _sha256(
        {
            "schema_version": CELL_HASH_VERSION,
            "range": range_a1,
            "rows": rows,
            "columns": columns,
            "cells": [
                [canonical_cell_payload(cell) for cell in row] for row in cells
            ],
        }
    )


def blank_range_hash(range_a1: str) -> str:
    """Return the digest a genuinely empty rectangle produces.

    Used to prove a target is untouched before writing into it, without the
    client having to send a grid of nulls.
    """

    rows, columns = a1_dimensions(range_a1)
    return range_hash(
        range_a1,
        tuple(tuple(BLANK_CELL for _ in range(columns)) for _ in range(rows)),
    )


def is_blank_grid(cells: tuple[tuple[CellState, ...], ...]) -> bool:
    return all(cell.is_blank for row in cells for cell in row)


def _sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "BLANK_CELL",
    "CELL_HASH_VERSION",
    "CellState",
    "blank_range_hash",
    "canonical_cell_payload",
    "cell_hash",
    "is_blank_grid",
    "range_hash",
]
