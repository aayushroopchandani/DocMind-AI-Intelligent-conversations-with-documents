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
    RangeHashBuilder,
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
from .compiler import (
    CompiledPatch,
    PatchCompilationError,
    PatchIdentity,
    compile_patch,
    formula_placement,
)
from .conflicts import (
    CONFLICT_MATRIX,
    ConflictAssessment,
    ConflictKind,
    ConflictResolution,
    assess_conflict,
    rebase_patch,
)
from .grid import MaterializedGrid, ResultGrid
from .inverse import InverseNotAvailableError, build_inverse, invert_operation
from .payloads import (
    BuiltPayload,
    PayloadStorageRequiredError,
    PayloadTooLargeError,
    PayloadWriter,
    build_payload,
    encode_chunk,
)
from .preview import PatchPreview, build_patch_preview
from .receipt import (
    OperationOutcome,
    OperationResult,
    PatchApplicationReceipt,
    ReceiptVerdict,
    TouchedRange,
    expected_post_hash,
    expected_pre_hash,
    touched_range_hash,
    verify_receipt,
)
from .undo import UndoNotAvailableError, build_undo_patch
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
    "BuiltPayload",
    "CELL_HASH_VERSION",
    "CONFLICT_MATRIX",
    "CellState",
    "ChunkedPayload",
    "CompiledPatch",
    "ConflictAssessment",
    "ConflictKind",
    "ConflictResolution",
    "InlinePayload",
    "InverseNotAvailableError",
    "MAX_INLINE_CELLS",
    "MaterializedGrid",
    "OperationOutcome",
    "OperationResult",
    "PATCH_COMPILER_VERSION",
    "PATCH_SCHEMA_VERSION",
    "PROPOSABLE_OPERATIONS",
    "PatchApplicationReceipt",
    "PatchCompilationError",
    "PatchIdentity",
    "PatchImpact",
    "PatchIssue",
    "PatchOperation",
    "PatchOperationType",
    "PatchPayload",
    "PatchPreview",
    "PatchStatus",
    "PayloadChunkReference",
    "PayloadStorageRequiredError",
    "PayloadTooLargeError",
    "PayloadWriter",
    "RESERVED_OPERATIONS",
    "RangeHashBuilder",
    "ReceiptVerdict",
    "ResultGrid",
    "SUPPORTED_OPERATIONS",
    "TouchedRange",
    "UndoNotAvailableError",
    "WorkbookGuard",
    "WorkbookPatch",
    "applicable_status",
    "assess_conflict",
    "blank_range_hash",
    "build_inverse",
    "build_patch_preview",
    "build_payload",
    "build_undo_patch",
    "canonical_cell_payload",
    "canonical_patch_payload",
    "cell_hash",
    "check_guards",
    "compile_patch",
    "compute_patch_hash",
    "encode_chunk",
    "expected_post_hash",
    "expected_pre_hash",
    "formula_placement",
    "invert_operation",
    "is_already_applied",
    "is_blank_grid",
    "operation_order",
    "range_hash",
    "rebase_patch",
    "summarize_impact",
    "touched_range_hash",
    "validate_patch",
    "verify_receipt",
]
