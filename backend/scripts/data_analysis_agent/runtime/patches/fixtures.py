"""Golden cell-hash fixtures, shared with the TypeScript implementation.

Defined once, here, and consumed by both sides:

* `tests/test_data_analysis_phase9_patches.py` asserts Python produces these;
* `frontend/my-app/lib/data-analysis/patch/cell-hash.fixtures.ts` carries the
  same digests for the browser implementation to assert against.

Run this module to regenerate the TypeScript file's expected values after any
deliberate change to the algorithm:

    backend/.venv/bin/python -m scripts.data_analysis_agent.runtime.patches.fixtures
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models.workbook import WorkbookCellType
from .cells import CellState, cell_hash, range_hash


@dataclass(frozen=True, slots=True)
class CellFixture:
    name: str
    cell: CellState


@dataclass(frozen=True, slots=True)
class RangeFixture:
    name: str
    range_a1: str
    cells: tuple[tuple[CellState, ...], ...]


CELL_FIXTURES: tuple[CellFixture, ...] = (
    CellFixture("blank", CellState()),
    CellFixture(
        "text",
        CellState(value="North", cell_type=WorkbookCellType.STRING),
    ),
    CellFixture(
        "integer",
        CellState(value=42, cell_type=WorkbookCellType.NUMBER),
    ),
    CellFixture(
        "float with binary representation",
        CellState(
            value=1.005,
            cell_type=WorkbookCellType.NUMBER,
            number_format="0.00",
        ),
    ),
    CellFixture(
        "negative zero normalizes",
        CellState(value=-0.0, cell_type=WorkbookCellType.NUMBER),
    ),
    CellFixture(
        "boolean",
        CellState(value=True, cell_type=WorkbookCellType.BOOLEAN),
    ),
    CellFixture(
        "formula",
        CellState(
            value=0.6,
            formula="=(C2-D2)/C2",
            cell_type=WorkbookCellType.NUMBER,
            number_format="0.00%",
        ),
    ),
    CellFixture(
        "merged and protected",
        CellState(
            value="Header",
            cell_type=WorkbookCellType.STRING,
            merged=True,
            protected=True,
        ),
    ),
    CellFixture(
        "empty string is not null",
        CellState(value="", cell_type=WorkbookCellType.STRING),
    ),
)


def _row(*cells: CellState) -> tuple[CellState, ...]:
    return cells


_HEADER_ROW = _row(
    CellState(value="Region", cell_type=WorkbookCellType.STRING),
    CellState(value="Revenue", cell_type=WorkbookCellType.STRING),
)
_DATA_ROW = _row(
    CellState(value="North", cell_type=WorkbookCellType.STRING),
    CellState(value=1234.5, cell_type=WorkbookCellType.NUMBER),
)

RANGE_FIXTURES: tuple[RangeFixture, ...] = (
    RangeFixture(
        "blank 2x2",
        "Sheet1!A1:B2",
        (_row(CellState(), CellState()), _row(CellState(), CellState())),
    ),
    RangeFixture("mixed 2x2", "Sheet1!A1:B2", (_HEADER_ROW, _DATA_ROW)),
    # Identical content, different address: the digest must differ.
    RangeFixture(
        "same content at a different address",
        "Sheet1!D1:E2",
        (_HEADER_ROW, _DATA_ROW),
    ),
)


def expected_cell_hashes() -> dict[str, str]:
    return {fixture.name: cell_hash(fixture.cell) for fixture in CELL_FIXTURES}


def expected_range_hashes() -> dict[str, str]:
    return {
        fixture.name: range_hash(fixture.range_a1, fixture.cells)
        for fixture in RANGE_FIXTURES
    }


def main() -> int:  # pragma: no cover - developer utility
    print("cell hashes:")
    for name, digest in expected_cell_hashes().items():
        print(f'  {name:40} "{digest}"')
    print("\nrange hashes:")
    for name, digest in expected_range_hashes().items():
        print(f'  {name:40} "{digest}"')
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "CELL_FIXTURES",
    "RANGE_FIXTURES",
    "CellFixture",
    "RangeFixture",
    "expected_cell_hashes",
    "expected_range_hashes",
]
