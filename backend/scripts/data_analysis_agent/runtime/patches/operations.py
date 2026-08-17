"""The operation envelope and its registry (Phase 9.10.2).

Every spreadsheet change is one of these — declarative data, never a Univer
command and never JavaScript. The frontend adapter reads the operation type and
calls the API it knows; nothing in a patch can name a function to invoke.

The registry is what keeps the protocol honest about its own reach. An operation
type may be *reserved* — present in the schema, rejected at validation — so the
plan and patch schemas can stay stable while the adapter catches up. Reserving
is better than omitting: a planner that proposes a chart gets
`unsupported_patch_operation` instead of a confusing schema error.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models.workbook import a1_dimensions
from .cells import CellState


MAX_INLINE_CELLS = 400
"""Above this a payload moves to blob storage (9.10.3)."""


class PatchOperationType(str, Enum):
    CREATE_SHEET = "create_sheet"
    RENAME_SHEET = "rename_sheet"
    WRITE_RANGE = "write_range"
    CLEAR_RANGE = "clear_range"
    SET_FORMULA = "set_formula"
    FILL_FORMULA = "fill_formula"
    SET_NUMBER_FORMAT = "set_number_format"
    DELETE_SHEET = "delete_sheet"

    # Reserved: present in the protocol, rejected until an adapter exists.
    CREATE_TABLE = "create_table"
    ATTACH_CHART = "attach_chart"
    ATTACH_IMAGE = "attach_image"
    INSERT_ROWS = "insert_rows"
    INSERT_COLUMNS = "insert_columns"


SUPPORTED_OPERATIONS: frozenset[PatchOperationType] = frozenset(
    {
        PatchOperationType.CREATE_SHEET,
        PatchOperationType.RENAME_SHEET,
        PatchOperationType.WRITE_RANGE,
        PatchOperationType.CLEAR_RANGE,
        PatchOperationType.SET_FORMULA,
        PatchOperationType.FILL_FORMULA,
        PatchOperationType.SET_NUMBER_FORMAT,
        # Only reachable through the controlled undo path, never proposed
        # directly by a plan (9.10.5).
        PatchOperationType.DELETE_SHEET,
    }
)

RESERVED_OPERATIONS: frozenset[PatchOperationType] = frozenset(
    PatchOperationType
).difference(SUPPORTED_OPERATIONS)

PROPOSABLE_OPERATIONS: frozenset[PatchOperationType] = SUPPORTED_OPERATIONS.difference(
    {PatchOperationType.DELETE_SHEET}
)


class PayloadChunkReference(BaseModel):
    """One bounded block of a large payload (9.10.3).

    Chunked by rows so the browser can apply a wide result without ever holding
    several full copies in memory. The patch hash commits to the ordered chunk
    checksums, so a swapped or truncated chunk changes the patch identity.
    """

    index: int = Field(ge=0)
    first_row: int = Field(ge=0)
    last_row: int = Field(ge=0)
    byte_count: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_key: str = Field(min_length=1, max_length=1024)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.last_row < self.first_row:
            raise ValueError("chunk last_row cannot precede first_row")
        return self


class InlinePayload(BaseModel):
    """A small grid carried directly in the patch."""

    kind: Literal["inline"] = "inline"
    cells: tuple[tuple[CellState, ...], ...] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_size(self) -> Self:
        columns = len(self.cells[0])
        if any(len(row) != columns for row in self.cells):
            raise ValueError("inline payload must be rectangular")
        if len(self.cells) * columns > MAX_INLINE_CELLS:
            raise ValueError(
                f"inline payloads are limited to {MAX_INLINE_CELLS} cells; "
                "use a chunked payload"
            )
        return self

    @property
    def cell_count(self) -> int:
        return len(self.cells) * len(self.cells[0])


class ChunkedPayload(BaseModel):
    """A large grid stored outside MongoDB and fetched by the adapter."""

    kind: Literal["chunked"] = "chunked"
    chunks: tuple[PayloadChunkReference, ...] = Field(min_length=1, max_length=1_000)
    total_rows: int = Field(ge=1)
    total_columns: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_chunks(self) -> Self:
        indexes = [chunk.index for chunk in self.chunks]
        if indexes != sorted(indexes) or len(set(indexes)) != len(indexes):
            raise ValueError("payload chunks must be uniquely and densely ordered")
        expected_row = 0
        for chunk in self.chunks:
            if chunk.first_row != expected_row:
                raise ValueError("payload chunks must cover contiguous rows")
            expected_row = chunk.last_row + 1
        if expected_row != self.total_rows:
            raise ValueError("payload chunks must cover every row exactly once")
        return self

    @property
    def cell_count(self) -> int:
        return self.total_rows * self.total_columns


PatchPayload = Annotated[
    InlinePayload | ChunkedPayload,
    Field(discriminator="kind"),
]


class PatchOperation(BaseModel):
    """One reviewable change to one place in the workbook."""

    op_id: str = Field(min_length=1, max_length=120)
    operation_type: PatchOperationType
    depends_on: tuple[str, ...] = Field(default=(), max_length=32)

    worksheet_id: str = Field(min_length=1, max_length=200)
    range_a1: str | None = Field(default=None, max_length=100)

    # What the target must look like before, and will look like after. Both are
    # checked by the adapter; a mismatch aborts before any mutation.
    expected_before_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    expected_after_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    payload: PatchPayload | None = None
    formula: str | None = Field(default=None, max_length=8_192)
    number_format: str | None = Field(default=None, max_length=120)
    sheet_name: str | None = Field(default=None, max_length=255)
    affected_cells: int = Field(default=0, ge=0)
    inverse_op_id: str | None = Field(default=None, max_length=120)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_operation(self) -> Self:
        kind = self.operation_type
        if kind in _RANGE_OPERATIONS and not self.range_a1:
            raise ValueError(f"'{kind.value}' requires a target range")
        if kind in _SHEET_NAME_OPERATIONS and not self.sheet_name:
            raise ValueError(f"'{kind.value}' requires a sheet name")
        if kind in _FORMULA_OPERATIONS and not self.formula:
            raise ValueError(f"'{kind.value}' requires a formula")
        if kind is PatchOperationType.SET_NUMBER_FORMAT and not self.number_format:
            raise ValueError("set_number_format requires a number format")
        if kind is PatchOperationType.WRITE_RANGE and self.payload is None:
            raise ValueError("write_range requires a payload")
        if kind is not PatchOperationType.WRITE_RANGE and self.payload is not None:
            raise ValueError(f"'{kind.value}' does not take a payload")
        if self.range_a1 is not None:
            rows, columns = a1_dimensions(self.range_a1)
            if self.payload is not None and self.payload.cell_count != rows * columns:
                raise ValueError("payload size does not match the target range")
            if self.affected_cells > rows * columns:
                raise ValueError("affected_cells exceeds the target range")
        return self

    @property
    def is_supported(self) -> bool:
        return self.operation_type in SUPPORTED_OPERATIONS


_RANGE_OPERATIONS = frozenset(
    {
        PatchOperationType.WRITE_RANGE,
        PatchOperationType.CLEAR_RANGE,
        PatchOperationType.SET_FORMULA,
        PatchOperationType.FILL_FORMULA,
        PatchOperationType.SET_NUMBER_FORMAT,
    }
)

_SHEET_NAME_OPERATIONS = frozenset(
    {
        PatchOperationType.CREATE_SHEET,
        PatchOperationType.RENAME_SHEET,
    }
)

_FORMULA_OPERATIONS = frozenset(
    {
        PatchOperationType.SET_FORMULA,
        PatchOperationType.FILL_FORMULA,
    }
)


__all__ = [
    "MAX_INLINE_CELLS",
    "PROPOSABLE_OPERATIONS",
    "RESERVED_OPERATIONS",
    "SUPPORTED_OPERATIONS",
    "ChunkedPayload",
    "InlinePayload",
    "PatchOperation",
    "PatchOperationType",
    "PatchPayload",
    "PayloadChunkReference",
]
