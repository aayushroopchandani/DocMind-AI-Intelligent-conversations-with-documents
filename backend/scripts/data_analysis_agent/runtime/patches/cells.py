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
from collections.abc import Sequence
from typing import Any, Self

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


class RangeHashBuilder:
    """The range hash, computed one row at a time.

    Byte-for-byte the same digest as hashing the whole grid at once — it emits
    exactly the JSON `json.dumps(..., sort_keys=True, separators=(",", ":"))`
    would produce for the same payload, just in pieces. That matters because the
    patch compiler hashes results it is simultaneously uploading in row blocks,
    and a 250,000-cell result should never need a second full copy of itself as
    a Python list and a third as a JSON string.

    The document is fixed and its keys sort to `cells, columns, range, rows,
    schema_version`, so the surrounding bytes are known before the first row
    arrives and only the `cells` array has to stream.
    """

    __slots__ = ("_columns", "_digest", "_range", "_result", "_rows", "_written")

    def __init__(self, range_a1: str) -> None:
        self._rows, self._columns = a1_dimensions(range_a1)
        self._range = range_a1
        self._digest = hashlib.sha256()
        self._digest.update(b'{"cells":[')
        self._written = 0
        self._result: str | None = None

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def columns(self) -> int:
        return self._columns

    def add_row(self, row: Sequence[CellState]) -> Self:
        if self._result is not None:
            raise ValueError("the range hash is already finalized")
        if len(row) != self._columns:
            raise ValueError(
                f"row has {len(row)} cells; {self._range} is "
                f"{self._columns} wide"
            )
        if self._written >= self._rows:
            raise ValueError(
                f"{self._range} holds {self._rows} rows; a further row was added"
            )
        parts = [b"[" if self._written == 0 else b",["]
        parts.append(
            b",".join(_encode(canonical_cell_payload(cell)) for cell in row)
        )
        parts.append(b"]")
        self._digest.update(b"".join(parts))
        self._written += 1
        return self

    def digest(self) -> str:
        """Return the finished digest; the builder accepts no more rows."""

        if self._result is None:
            if self._written != self._rows:
                raise ValueError(
                    f"{self._range} needs {self._rows} rows; "
                    f"{self._written} were added"
                )
            self._digest.update(
                b'],"columns":%d,"range":%s,"rows":%d,"schema_version":%s}'
                % (
                    self._columns,
                    _encode(self._range),
                    self._rows,
                    _encode(CELL_HASH_VERSION),
                )
            )
            self._result = self._digest.hexdigest()
        return self._result


def range_hash(
    range_a1: str,
    cells: tuple[tuple[CellState, ...], ...],
) -> str:
    """Return the digest for a rectangle of cells.

    The range is part of the digest, so the same values at a different address
    hash differently — a guard must not pass because the right content happened
    to exist somewhere else.
    """

    builder = RangeHashBuilder(range_a1)
    if len(cells) != builder.rows or any(
        len(row) != builder.columns for row in cells
    ):
        raise ValueError(
            f"cell grid does not match {range_a1} "
            f"({builder.rows}x{builder.columns})"
        )
    for row in cells:
        builder.add_row(row)
    return builder.digest()


def blank_range_hash(range_a1: str) -> str:
    """Return the digest a genuinely empty rectangle produces.

    Used to prove a target is untouched before writing into it, without the
    client having to send a grid of nulls — and without the server building one
    either, since every row of a blank rectangle is the same row.
    """

    builder = RangeHashBuilder(range_a1)
    blank_row = (BLANK_CELL,) * builder.columns
    for _ in range(builder.rows):
        builder.add_row(blank_row)
    return builder.digest()


def is_blank_grid(cells: tuple[tuple[CellState, ...], ...]) -> bool:
    return all(cell.is_blank for row in cells for cell in row)


def _encode(payload: Any) -> bytes:
    """Return the one canonical JSON encoding both languages agree on."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_encode(payload)).hexdigest()


__all__ = [
    "BLANK_CELL",
    "CELL_HASH_VERSION",
    "CellState",
    "RangeHashBuilder",
    "blank_range_hash",
    "canonical_cell_payload",
    "cell_hash",
    "is_blank_grid",
    "range_hash",
]
