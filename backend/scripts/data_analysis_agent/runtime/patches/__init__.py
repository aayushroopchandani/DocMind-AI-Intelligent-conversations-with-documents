"""Workbook Patch Protocol v1 (Phase 9.10).

Every spreadsheet change is declarative data: what to touch, what must be there
first, what will be there after, and how to undo it. Nothing in a patch names a
function to call, so a patch can never carry JavaScript or a Univer command —
the frontend adapter reads an operation type and calls the API it already knows.

Deliberately independent of both Univer and the execution engine. The adapter
lives in the frontend; this package only produces and checks the data.
"""

from .cells import (
    CELL_HASH_VERSION,
    BLANK_CELL,
    CellState,
    blank_range_hash,
    canonical_cell_payload,
    cell_hash,
    is_blank_grid,
    range_hash,
)
from .envelope import (
    PATCH_COMPILER_VERSION,
    PATCH_SCHEMA_VERSION,
    PatchImpact,
    PatchStatus,
    WorkbookGuard,
    WorkbookPatch,
    canonical_patch_payload,
    compute_patch_hash,
    summarize_impact,
)
from .inverse import InverseNotAvailableError, build_inverse, invert_operation
from .operations import (
    MAX_INLINE_CELLS,
    PROPOSABLE_OPERATIONS,
    RESERVED_OPERATIONS,
    SUPPORTED_OPERATIONS,
    ChunkedPayload,
    InlinePayload,
    PatchOperation,
    PatchOperationType,
    PatchPayload,
    PayloadChunkReference,
)
from .validation import (
    PatchIssue,
    applicable_status,
    check_guards,
    is_already_applied,
    operation_order,
    validate_patch,
)

__all__ = [
    "BLANK_CELL",
    "CELL_HASH_VERSION",
    "MAX_INLINE_CELLS",
    "PATCH_COMPILER_VERSION",
    "PATCH_SCHEMA_VERSION",
    "PROPOSABLE_OPERATIONS",
    "RESERVED_OPERATIONS",
    "SUPPORTED_OPERATIONS",
    "CellState",
    "ChunkedPayload",
    "InlinePayload",
    "InverseNotAvailableError",
    "PatchImpact",
    "PatchIssue",
    "PatchOperation",
    "PatchOperationType",
    "PatchPayload",
    "PatchStatus",
    "PayloadChunkReference",
    "WorkbookGuard",
    "WorkbookPatch",
    "applicable_status",
    "blank_range_hash",
    "build_inverse",
    "canonical_cell_payload",
    "canonical_patch_payload",
    "cell_hash",
    "check_guards",
    "compute_patch_hash",
    "invert_operation",
    "is_already_applied",
    "is_blank_grid",
    "operation_order",
    "range_hash",
    "summarize_impact",
    "validate_patch",
]
