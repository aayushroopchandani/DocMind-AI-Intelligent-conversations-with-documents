"""Is this rectangle safe to write into? (Phase 9.11.2, step 5)

Answered from rectangles wherever rectangles can answer it. A sheet's used range
and its merge/table/protection/drawing intervals are enough to rule a target in
or out most of the time, and interval arithmetic over a few hundred rectangles
costs nothing compared with reading a sheet cell by cell — which the plan
forbids outright.

Cells are only consulted when the target actually overlaps existing content, and
then only the captured rectangle, never the sheet.

The one rule that must not bend: silence is not permission. A target that
overlaps the used range but was never captured returns `uncaptured`, not "looks
fine". The backend cannot see the workbook, so an unanswered question stays
unanswered.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..models.workbook import Rect
from ..patches.cells import CellState
from .context import CapturedRange, SheetOccupancy


class CollisionKind(str, Enum):
    OUT_OF_BOUNDS = "out_of_bounds"
    UNCAPTURED = "uncaptured"
    VALUES = "values"
    FORMULAS = "formulas"
    MERGED = "merged"
    PROTECTED = "protected"
    TABLE = "table"
    DRAWING = "drawing"
    RESERVED = "reserved"


@dataclass(frozen=True, slots=True)
class PlacementCollision:
    """One reason a rectangle cannot be written."""

    kind: CollisionKind
    range_a1: str
    detail: str

    @property
    def blocks_relocation(self) -> bool:
        """Whether moving to a fresh sheet would not help.

        A rectangle past the sheet's own limits is the one collision a new sheet
        does not fix: the result is simply too large for a worksheet.
        """

        return self.kind is CollisionKind.OUT_OF_BOUNDS


@dataclass(frozen=True, slots=True)
class OccupancyReport:
    """What a target rectangle looks like right now."""

    rect: Rect
    collisions: tuple[PlacementCollision, ...]
    provably_blank: bool
    capture: CapturedRange | None

    @property
    def is_free(self) -> bool:
        return not self.collisions


def inspect_rectangle(
    rect: Rect,
    *,
    sheet: SheetOccupancy,
    capture: CapturedRange | None,
    reserved: tuple[tuple[Rect, str], ...] = (),
) -> OccupancyReport:
    """Report every reason `rect` on `sheet` is not free to write.

    `reserved` carries active write reservations held by other runs, each with
    the identifier to name in the explanation.
    """

    collisions: list[PlacementCollision] = []
    target = rect.to_a1()

    if not rect.within_sheet_limits or not sheet.limit.contains(rect):
        collisions.append(
            PlacementCollision(
                kind=CollisionKind.OUT_OF_BOUNDS,
                range_a1=target,
                detail=(
                    f"{target} does not fit inside "
                    f"{sheet.worksheet_name} "
                    f"({sheet.row_count}x{sheet.column_count})"
                ),
            )
        )
        # Nothing else is meaningful about a rectangle off the end of the sheet.
        return OccupancyReport(
            rect=rect,
            collisions=tuple(collisions),
            provably_blank=False,
            capture=None,
        )

    collisions.extend(_structure_collisions(rect, sheet))
    for other, owner in reserved:
        if rect.intersects(other):
            collisions.append(
                PlacementCollision(
                    kind=CollisionKind.RESERVED,
                    range_a1=other.to_a1(),
                    detail=f"{other.to_a1()} is reserved by {owner}",
                )
            )

    used = sheet.used_rect
    # Outside the used range there is nothing to read: the sheet itself says the
    # area holds no content, so no capture is required to prove it.
    outside_content = used is None or not rect.intersects(used)
    if outside_content:
        return OccupancyReport(
            rect=rect,
            collisions=tuple(collisions),
            provably_blank=True,
            capture=capture,
        )

    if capture is None:
        collisions.append(
            PlacementCollision(
                kind=CollisionKind.UNCAPTURED,
                range_a1=target,
                detail=(
                    f"{target} overlaps used content on "
                    f"{sheet.worksheet_name} and was not captured"
                ),
            )
        )
        return OccupancyReport(
            rect=rect,
            collisions=tuple(collisions),
            provably_blank=False,
            capture=None,
        )

    collisions.extend(_cell_collisions(rect, capture))
    return OccupancyReport(
        rect=rect,
        collisions=tuple(collisions),
        provably_blank=not any(
            item.kind in {CollisionKind.VALUES, CollisionKind.FORMULAS}
            for item in collisions
        ),
        capture=capture,
    )


def _structure_collisions(
    rect: Rect,
    sheet: SheetOccupancy,
) -> tuple[PlacementCollision, ...]:
    groups = (
        (CollisionKind.MERGED, sheet.merged_ranges, "merged cells"),
        (CollisionKind.PROTECTED, sheet.protected_ranges, "a protected range"),
        (CollisionKind.TABLE, sheet.table_ranges, "a structured table"),
        (CollisionKind.DRAWING, sheet.drawing_ranges, "a drawing"),
    )
    collisions: list[PlacementCollision] = []
    for kind, ranges, label in groups:
        for value in ranges:
            other = Rect.from_a1(value)
            if rect.intersects(other):
                collisions.append(
                    PlacementCollision(
                        kind=kind,
                        range_a1=value,
                        detail=f"{value} contains {label}",
                    )
                )
    return tuple(collisions)


def _cell_collisions(
    rect: Rect,
    capture: CapturedRange,
) -> tuple[PlacementCollision, ...]:
    """Report occupied cells inside `rect`, reading only the captured window.

    Stops at the first value and the first formula: the reviewer needs to know
    the target is occupied and where, not an inventory of every cell in it.
    """

    origin = capture.rect
    row_offset = rect.first_row - origin.first_row
    column_offset = rect.first_column - origin.first_column
    values: PlacementCollision | None = None
    formulas: PlacementCollision | None = None
    for row_index in range(rect.rows):
        row = capture.cells[row_offset + row_index]
        for column_index in range(rect.columns):
            cell = row[column_offset + column_index]
            if cell.is_blank:
                continue
            address = Rect(
                first_row=rect.first_row + row_index,
                first_column=rect.first_column + column_index,
                last_row=rect.first_row + row_index,
                last_column=rect.first_column + column_index,
            ).to_a1()
            if cell.formula and formulas is None:
                formulas = PlacementCollision(
                    kind=CollisionKind.FORMULAS,
                    range_a1=address,
                    detail=f"{address} holds a formula",
                )
            elif cell.value is not None and values is None:
                values = PlacementCollision(
                    kind=CollisionKind.VALUES,
                    range_a1=address,
                    detail=f"{address} holds a value",
                )
            elif cell.merged or cell.protected:
                if values is None:
                    values = PlacementCollision(
                        kind=CollisionKind.VALUES,
                        range_a1=address,
                        detail=f"{address} is merged or protected",
                    )
            if values is not None and formulas is not None:
                return (values, formulas)
    return tuple(item for item in (values, formulas) if item is not None)


def subgrid(
    capture: CapturedRange,
    rect: Rect,
) -> tuple[tuple[CellState, ...], ...]:
    """Return the cells of `rect` out of a capture that contains it."""

    origin = capture.rect
    if not origin.contains(rect):
        raise ValueError(f"{rect.to_a1()} is not inside {capture.range_a1}")
    row_offset = rect.first_row - origin.first_row
    column_offset = rect.first_column - origin.first_column
    return tuple(
        capture.cells[row_offset + index][
            column_offset : column_offset + rect.columns
        ]
        for index in range(rect.rows)
    )


__all__ = [
    "CollisionKind",
    "OccupancyReport",
    "PlacementCollision",
    "inspect_rectangle",
    "subgrid",
]
