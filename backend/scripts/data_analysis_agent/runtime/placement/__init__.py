"""Post-execution placement and write reservations (Phase 9.11).

The backend knows what the result is. Only the browser knows what the workbook
looks like right now. This package is the handshake between the two: the client
sends one hashed, bounded view of the live workbook, and these modules decide —
deterministically, from rectangles rather than cell scans — where the result may
safely go, then hold that rectangle until the patch is applied or abandoned.

    context     what the client captured, and its hash
    occupancy   whether a rectangle is free, and why not
    naming      a sheet name the workbook will accept
    selection   the adjacent-right / new-sheet / exact-range decision
    reservations  keeping the chosen rectangle claimed while review happens
"""

from .context import (
    CONTEXT_SCHEMA_VERSION,
    MAX_CAPTURED_CELLS,
    MAX_CAPTURED_RANGES,
    CapturedRange,
    SheetOccupancy,
    WorkbookPatchContext,
    canonical_context_payload,
    compute_context_hash,
)
from .naming import (
    DEFAULT_SHEET_NAME,
    MAX_SHEET_NAME_LENGTH,
    deterministic_worksheet_id,
    sanitize_sheet_name,
    unique_sheet_name,
)
from .occupancy import (
    CollisionKind,
    OccupancyReport,
    PlacementCollision,
    inspect_rectangle,
    subgrid,
)
from .reservations import (
    DEFAULT_RESERVATION_SECONDS,
    ReservationRequest,
    WriteReservationService,
)
from .selection import (
    PlacementDecision,
    PlacementError,
    PlacementFailure,
    PlacementRequest,
    select_placement,
)

__all__ = [
    "CONTEXT_SCHEMA_VERSION",
    "DEFAULT_RESERVATION_SECONDS",
    "DEFAULT_SHEET_NAME",
    "MAX_CAPTURED_CELLS",
    "MAX_CAPTURED_RANGES",
    "MAX_SHEET_NAME_LENGTH",
    "CapturedRange",
    "CollisionKind",
    "OccupancyReport",
    "PlacementCollision",
    "PlacementDecision",
    "PlacementError",
    "PlacementFailure",
    "PlacementRequest",
    "ReservationRequest",
    "SheetOccupancy",
    "WorkbookPatchContext",
    "WriteReservationService",
    "canonical_context_payload",
    "compute_context_hash",
    "deterministic_worksheet_id",
    "inspect_rectangle",
    "sanitize_sheet_name",
    "select_placement",
    "subgrid",
    "unique_sheet_name",
]
