"""Choosing where a result goes (Phase 9.11.2 – 9.11.4).

The decision is made once, after the output's exact dimensions are known and
against one hashed view of the live workbook. It is deterministic: the same
result and the same context always select the same target, which is what makes a
patch safe to recompile after a rebase.

The ordering of the checks is the safety property. A rectangle is only selected
after it has been proven to fit the sheet, to be free of values, formulas,
merges, protection, tables, drawings and other runs' reservations, and — when it
does overlap content — to have been explicitly approved for replacement with its
previous contents captured. Anything short of that relocates rather than
overwrites. There is no path through this module that writes over data because
nobody objected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..models.plans import WorkbookCollisionPolicy, WorkbookPlacementPolicy
from ..models.workbook import (
    MAX_WORKBOOK_COLUMNS,
    MAX_WORKBOOK_ROWS,
    Rect,
)
from ..patches.cells import CellState, blank_range_hash, range_hash
from .context import WorkbookPatchContext
from .naming import (
    DEFAULT_SHEET_NAME,
    deterministic_worksheet_id,
    unique_sheet_name,
)
from .occupancy import (
    CollisionKind,
    OccupancyReport,
    PlacementCollision,
    inspect_rectangle,
    subgrid,
)


#: Collisions a fresh sheet cannot resolve, so relocating would only repeat them.
_UNRELOCATABLE = frozenset({CollisionKind.OUT_OF_BOUNDS})

#: Collisions that an explicitly approved replacement is allowed to write over.
#: Structure — merges, protection, tables, drawings — is never in this set: a
#: patch cannot restore it, so it cannot destroy it either.
_OVERWRITABLE = frozenset({CollisionKind.VALUES, CollisionKind.FORMULAS})


class PlacementFailure(str, Enum):
    UNKNOWN_SOURCE_SHEET = "unknown_source_sheet"
    UNKNOWN_TARGET_SHEET = "unknown_target_sheet"
    OUTPUT_EXCEEDS_SHEET = "output_exceeds_sheet"
    TARGET_OCCUPIED = "target_occupied"
    TARGET_NOT_CAPTURED = "target_not_captured"
    REPLACEMENT_NOT_APPROVED = "replacement_not_approved"
    EXACT_TARGET_TOO_SMALL = "exact_target_too_small"


class PlacementError(RuntimeError):
    """No safe target exists for this result."""

    def __init__(
        self,
        code: PlacementFailure,
        message: str,
        *,
        collisions: tuple[PlacementCollision, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.collisions = collisions


@dataclass(frozen=True, slots=True)
class PlacementRequest:
    """Everything the decision depends on, and nothing else."""

    workbook_id: str
    policy: WorkbookPlacementPolicy
    source_worksheet_id: str
    source_range_a1: str
    output_rows: int
    output_columns: int
    collision_policy: WorkbookCollisionPolicy = (
        WorkbookCollisionPolicy.REQUIRE_REAPPROVAL
    )
    minimum_column_gap: int = 2
    exact_target_range_a1: str | None = None
    # Set only when the user asked for replacement *and* the destructive plan
    # was approved up front. Both are required by 9.11.4; one alone is not.
    replacement_requested: bool = False
    early_destructive_approval: bool = False
    sheet_name_hint: str = DEFAULT_SHEET_NAME
    #: Extra identity mixed into a generated sheet ID so two runs writing the
    #: same-named sheet do not collide on it.
    identity: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.output_rows < 1 or self.output_columns < 1:
            raise ValueError("output dimensions must be positive")
        if not 0 <= self.minimum_column_gap <= 100:
            raise ValueError("minimum_column_gap must be between 0 and 100")

    @property
    def may_overwrite(self) -> bool:
        return self.replacement_requested and self.early_destructive_approval


@dataclass(frozen=True, slots=True)
class PlacementDecision:
    """Where the result goes, and what is there now."""

    policy_used: WorkbookPlacementPolicy
    worksheet_id: str
    worksheet_name: str
    target_range_a1: str
    target_rect: Rect
    creates_sheet: bool
    overwrites: bool
    relocated: bool
    before_hash: str
    explanation: str
    before_cells: tuple[tuple[CellState, ...], ...] | None = None
    collisions: tuple[PlacementCollision, ...] = ()

    @property
    def cell_count(self) -> int:
        return self.target_rect.cell_count


def select_placement(
    request: PlacementRequest,
    *,
    context: WorkbookPatchContext,
    reserved: dict[str, tuple[tuple[Rect, str], ...]] | None = None,
) -> PlacementDecision:
    """Return the one target this result may be written to."""

    reservations = reserved or {}
    if request.policy is WorkbookPlacementPolicy.NEW_SHEET:
        return _new_sheet(request, context=context, relocated=False, collisions=())
    if request.policy is WorkbookPlacementPolicy.EXACT_RANGE:
        return _exact_range(request, context=context, reserved=reservations)
    return _adjacent_right(request, context=context, reserved=reservations)


def _adjacent_right(
    request: PlacementRequest,
    *,
    context: WorkbookPatchContext,
    reserved: dict[str, tuple[tuple[Rect, str], ...]],
) -> PlacementDecision:
    sheet = context.sheet(request.source_worksheet_id)
    if sheet is None:
        raise PlacementError(
            PlacementFailure.UNKNOWN_SOURCE_SHEET,
            "the captured context does not include the source worksheet",
        )
    source = Rect.from_a1(request.source_range_a1)
    # An offset from the right edge, so the default of 2 leaves exactly one
    # empty column between the source table and the result.
    first_column = source.last_column + max(1, request.minimum_column_gap)
    if (
        first_column + request.output_columns - 1 > MAX_WORKBOOK_COLUMNS
        or source.first_row + request.output_rows - 1 > MAX_WORKBOOK_ROWS
    ):
        return _new_sheet(
            request,
            context=context,
            relocated=True,
            collisions=(
                PlacementCollision(
                    kind=CollisionKind.OUT_OF_BOUNDS,
                    range_a1=request.source_range_a1,
                    detail="the result does not fit beside the source table",
                ),
            ),
        )
    target = Rect.sized(
        first_row=source.first_row,
        first_column=first_column,
        rows=request.output_rows,
        columns=request.output_columns,
    )
    report = inspect_rectangle(
        target,
        sheet=sheet,
        capture=context.capture_for(target, worksheet_id=sheet.worksheet_id),
        reserved=reserved.get(sheet.worksheet_id, ()),
    )
    if report.is_free:
        return _decision(
            request,
            policy=WorkbookPlacementPolicy.ADJACENT_RIGHT,
            sheet_id=sheet.worksheet_id,
            sheet_name=sheet.worksheet_name,
            report=report,
            overwrites=False,
            relocated=False,
            explanation=(
                f"Placed beside {sheet.worksheet_name}!"
                f"{source.to_a1()}, starting at column "
                f"{target.to_a1().split(':')[0]}."
            ),
        )
    _refuse_if_forbidden(request, report)
    return _new_sheet(
        request,
        context=context,
        relocated=True,
        collisions=report.collisions,
    )


def _exact_range(
    request: PlacementRequest,
    *,
    context: WorkbookPatchContext,
    reserved: dict[str, tuple[tuple[Rect, str], ...]],
) -> PlacementDecision:
    if request.exact_target_range_a1 is None:  # pragma: no cover - model guards it
        raise PlacementError(
            PlacementFailure.UNKNOWN_TARGET_SHEET,
            "exact-range placement requires a target range",
        )
    sheet = context.sheet(request.source_worksheet_id)
    if sheet is None:
        raise PlacementError(
            PlacementFailure.UNKNOWN_TARGET_SHEET,
            "the captured context does not include the target worksheet",
        )
    declared = Rect.from_a1(request.exact_target_range_a1)
    # Anchored at the declared top-left; the result must stay inside the
    # rectangle the user actually approved, never spill past it.
    target = Rect.sized(
        first_row=declared.first_row,
        first_column=declared.first_column,
        rows=request.output_rows,
        columns=request.output_columns,
    )
    if not declared.contains(target):
        raise PlacementError(
            PlacementFailure.EXACT_TARGET_TOO_SMALL,
            (
                f"the result is {request.output_rows}x{request.output_columns} "
                f"and does not fit the requested "
                f"{request.exact_target_range_a1}"
            ),
        )
    report = inspect_rectangle(
        target,
        sheet=sheet,
        capture=context.capture_for(target, worksheet_id=sheet.worksheet_id),
        reserved=reserved.get(sheet.worksheet_id, ()),
    )
    if report.is_free:
        return _decision(
            request,
            policy=WorkbookPlacementPolicy.EXACT_RANGE,
            sheet_id=sheet.worksheet_id,
            sheet_name=sheet.worksheet_name,
            report=report,
            overwrites=False,
            relocated=False,
            explanation=(
                f"Written to the requested range {sheet.worksheet_name}!"
                f"{target.to_a1()}, which is empty."
            ),
        )
    _refuse_if_forbidden(request, report)
    kinds = {item.kind for item in report.collisions}
    if kinds <= _OVERWRITABLE and request.may_overwrite:
        if report.capture is None:  # pragma: no cover - occupancy guarantees it
            raise PlacementError(
                PlacementFailure.TARGET_NOT_CAPTURED,
                "replacing content requires the previous cells to be captured",
                collisions=report.collisions,
            )
        return _decision(
            request,
            policy=WorkbookPlacementPolicy.EXACT_RANGE,
            sheet_id=sheet.worksheet_id,
            sheet_name=sheet.worksheet_name,
            report=report,
            overwrites=True,
            relocated=False,
            explanation=(
                f"Replacing {report.rect.cell_count} cells in "
                f"{sheet.worksheet_name}!{target.to_a1()}, as requested. "
                "The previous contents are captured so the edit can be undone."
            ),
        )
    if kinds <= _OVERWRITABLE and request.replacement_requested:
        raise PlacementError(
            PlacementFailure.REPLACEMENT_NOT_APPROVED,
            (
                "replacing existing content needs the destructive plan to be "
                "approved before execution"
            ),
            collisions=report.collisions,
        )
    return _new_sheet(
        request,
        context=context,
        relocated=True,
        collisions=report.collisions,
    )


def _new_sheet(
    request: PlacementRequest,
    *,
    context: WorkbookPatchContext,
    relocated: bool,
    collisions: tuple[PlacementCollision, ...],
) -> PlacementDecision:
    if (
        request.output_rows > MAX_WORKBOOK_ROWS
        or request.output_columns > MAX_WORKBOOK_COLUMNS
    ):
        raise PlacementError(
            PlacementFailure.OUTPUT_EXCEEDS_SHEET,
            (
                f"a {request.output_rows}x{request.output_columns} result does "
                "not fit on a worksheet"
            ),
            collisions=collisions,
        )
    if relocated and request.collision_policy is WorkbookCollisionPolicy.FAIL:
        raise PlacementError(
            PlacementFailure.TARGET_OCCUPIED,
            _occupied_message(collisions),
            collisions=collisions,
        )
    name = unique_sheet_name(
        request.sheet_name_hint,
        context.worksheet_names,
    )
    worksheet_id = deterministic_worksheet_id(
        request.workbook_id,
        name,
        *request.identity,
    )
    target = Rect.sized(
        first_row=1,
        first_column=1,
        rows=request.output_rows,
        columns=request.output_columns,
    )
    range_a1 = target.to_a1(sheet_name=name)
    explanation = (
        f"Placed on a new sheet '{name}' because {_occupied_message(collisions)}"
        if relocated
        else f"Placed on a new sheet '{name}'."
    )
    return PlacementDecision(
        policy_used=WorkbookPlacementPolicy.NEW_SHEET,
        worksheet_id=worksheet_id,
        worksheet_name=name,
        target_range_a1=range_a1,
        target_rect=target,
        creates_sheet=True,
        overwrites=False,
        relocated=relocated,
        # A sheet that does not exist yet has no content; the adapter creates it
        # empty in the same patch, so the guard is the empty-rectangle digest.
        before_hash=blank_range_hash(range_a1),
        explanation=explanation,
        before_cells=None,
        collisions=collisions,
    )


def _decision(
    request: PlacementRequest,
    *,
    policy: WorkbookPlacementPolicy,
    sheet_id: str,
    sheet_name: str,
    report: OccupancyReport,
    overwrites: bool,
    relocated: bool,
    explanation: str,
) -> PlacementDecision:
    range_a1 = report.rect.to_a1(sheet_name=sheet_name)
    before_cells: tuple[tuple[CellState, ...], ...] | None = None
    if report.capture is not None and not report.provably_blank:
        before_cells = subgrid(report.capture, report.rect)
    before_hash = (
        range_hash(range_a1, before_cells)
        if before_cells is not None
        else blank_range_hash(range_a1)
    )
    return PlacementDecision(
        policy_used=policy,
        worksheet_id=sheet_id,
        worksheet_name=sheet_name,
        target_range_a1=range_a1,
        target_rect=report.rect,
        creates_sheet=False,
        overwrites=overwrites,
        relocated=relocated,
        before_hash=before_hash,
        explanation=explanation,
        before_cells=before_cells,
        collisions=report.collisions,
    )


def _refuse_if_forbidden(
    request: PlacementRequest,
    report: OccupancyReport,
) -> None:
    """Stop before relocating when relocation cannot possibly help."""

    blocking = tuple(
        item for item in report.collisions if item.kind in _UNRELOCATABLE
    )
    if blocking:
        raise PlacementError(
            PlacementFailure.OUTPUT_EXCEEDS_SHEET,
            blocking[0].detail,
            collisions=report.collisions,
        )


def _occupied_message(collisions: tuple[PlacementCollision, ...]) -> str:
    if not collisions:
        return "the requested placement was unavailable."
    first = collisions[0]
    if first.kind is CollisionKind.UNCAPTURED:
        return f"{first.range_a1} could not be verified as empty."
    return f"{first.detail}."


__all__ = [
    "PlacementDecision",
    "PlacementError",
    "PlacementFailure",
    "PlacementRequest",
    "select_placement",
]
