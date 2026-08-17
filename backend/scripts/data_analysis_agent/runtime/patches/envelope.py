"""The patch envelope and its canonical hash (Phase 9.10.1).

A patch is the complete, reviewable description of a workbook change: what it
touches, what it expects to find there, what it will leave behind, and how to
undo it. The user approves a `patch_hash`, and that hash has to mean something
precise — so it commits to every byte that changes the outcome, including the
ordered checksums of any chunked payload, and to the tenant that owns it.

The plan document notes ownership fields "may be omitted from the canonical hash
if the hash is already bound to a tenant-scoped record" and then recommends
including them anyway. It is included here. A patch hash that is identical
across two workspaces would let an approval from one be replayed against the
other, and the cost of including two strings is nothing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models.canonical import canonical_content
from .operations import PatchOperation, PatchOperationType


PATCH_SCHEMA_VERSION = "1.0"
PATCH_COMPILER_VERSION = "1.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PatchStatus(str, Enum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    EXPIRED = "expired"


class WorkbookGuard(BaseModel):
    """What the workbook must still look like for this patch to be valid.

    A source guard protects the data the result was computed from; a target
    guard protects the rectangle about to be written. Both are checked live by
    the adapter immediately before mutation, because the backend cannot see the
    browser's workbook.
    """

    worksheet_id: str = Field(min_length=1, max_length=200)
    range_a1: str = Field(min_length=5, max_length=100)
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: str = Field(default="target", max_length=20)

    model_config = ConfigDict(extra="forbid", frozen=True)


class PatchImpact(BaseModel):
    """Bounded summary a reviewer reads before approving."""

    sheets_created: int = Field(default=0, ge=0)
    sheets_renamed: int = Field(default=0, ge=0)
    cells_written: int = Field(default=0, ge=0)
    cells_cleared: int = Field(default=0, ge=0)
    formulas_written: int = Field(default=0, ge=0)
    formats_changed: int = Field(default=0, ge=0)
    overwrites_existing_values: bool = False
    overwrites_existing_formulas: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)

    @property
    def total_cells(self) -> int:
        return self.cells_written + self.cells_cleared + self.formats_changed


class WorkbookPatch(BaseModel):
    """One declarative, reviewable workbook change."""

    patch_schema_version: str = PATCH_SCHEMA_VERSION
    patch_id: str = Field(min_length=1, max_length=120)
    patch_revision: int = Field(default=1, ge=1)
    patch_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    user_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=36, max_length=36)
    plan_id: str = Field(min_length=36, max_length=36)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_id: str = Field(min_length=1, max_length=120)

    workbook_id: str = Field(min_length=1, max_length=200)
    base_workbook_revision: int = Field(ge=0)
    source_guards: tuple[WorkbookGuard, ...] = Field(default=(), max_length=24)
    target_guards: tuple[WorkbookGuard, ...] = Field(default=(), max_length=24)

    operations: tuple[PatchOperation, ...] = Field(min_length=1, max_length=200)
    inverse_operations: tuple[PatchOperation, ...] = Field(
        default=(),
        max_length=200,
    )

    impact: PatchImpact
    maximum_affected_cells: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    compiler_version: str = PATCH_COMPILER_VERSION
    cell_hash_version: str = Field(min_length=1, max_length=20)

    status: PatchStatus = PatchStatus.DRAFT
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        op_ids = [operation.op_id for operation in self.operations]
        if len(op_ids) != len(set(op_ids)):
            raise ValueError("patch operation IDs must be unique")
        known = set(op_ids)
        for operation in self.operations:
            unknown = set(operation.depends_on).difference(known)
            if unknown:
                raise ValueError(
                    "operation depends on unknown ops: " + ", ".join(sorted(unknown))
                )
        inverse_ids = [operation.op_id for operation in self.inverse_operations]
        if len(inverse_ids) != len(set(inverse_ids)):
            raise ValueError("inverse operation IDs must be unique")
        if self.impact.total_cells > self.maximum_affected_cells:
            raise ValueError("impact exceeds the declared maximum affected cells")
        if self.status is PatchStatus.APPROVED and self.expires_at is None:
            raise ValueError("an approved patch must carry an expiry")
        return self

    @property
    def affected_cells(self) -> int:
        return sum(operation.affected_cells for operation in self.operations)

    @property
    def is_reversible(self) -> bool:
        return bool(self.inverse_operations)


def canonical_patch_payload(patch: WorkbookPatch) -> dict[str, Any]:
    """Return exactly what the patch hash commits to.

    Excluded: `patch_hash` itself, `status`, `expires_at` and `created_at` —
    lifecycle and timing, which must not change the identity of the change
    being approved.
    """

    return {
        "patch_schema_version": patch.patch_schema_version,
        "patch_id": patch.patch_id,
        "patch_revision": patch.patch_revision,
        # Tenant identity is inside the hash so an approval cannot be replayed
        # against a different workspace.
        "user_id": patch.user_id,
        "workspace_id": patch.workspace_id,
        "run_id": patch.run_id,
        "plan_id": patch.plan_id,
        "plan_hash": patch.plan_hash,
        "execution_id": patch.execution_id,
        "workbook_id": patch.workbook_id,
        "base_workbook_revision": patch.base_workbook_revision,
        "source_guards": [canonical_content(item) for item in patch.source_guards],
        "target_guards": [canonical_content(item) for item in patch.target_guards],
        "operations": [canonical_content(item) for item in patch.operations],
        "inverse_operations": [
            canonical_content(item) for item in patch.inverse_operations
        ],
        "impact": canonical_content(patch.impact),
        "maximum_affected_cells": patch.maximum_affected_cells,
        "idempotency_key": patch.idempotency_key,
        "compiler_version": patch.compiler_version,
        "cell_hash_version": patch.cell_hash_version,
    }


def compute_patch_hash(patch: WorkbookPatch) -> str:
    """Return the canonical hash for `patch`."""

    encoded = json.dumps(
        canonical_patch_payload(patch),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def summarize_impact(operations: tuple[PatchOperation, ...]) -> PatchImpact:
    """Derive the reviewer-facing summary from the operations themselves."""

    counters = {
        PatchOperationType.CREATE_SHEET: 0,
        PatchOperationType.RENAME_SHEET: 0,
        PatchOperationType.WRITE_RANGE: 0,
        PatchOperationType.CLEAR_RANGE: 0,
        PatchOperationType.SET_NUMBER_FORMAT: 0,
    }
    formulas = 0
    for operation in operations:
        kind = operation.operation_type
        if kind in counters:
            counters[kind] += (
                1
                if kind
                in {
                    PatchOperationType.CREATE_SHEET,
                    PatchOperationType.RENAME_SHEET,
                }
                else operation.affected_cells
            )
        elif kind in {
            PatchOperationType.SET_FORMULA,
            PatchOperationType.FILL_FORMULA,
        }:
            formulas += operation.affected_cells
    return PatchImpact(
        sheets_created=counters[PatchOperationType.CREATE_SHEET],
        sheets_renamed=counters[PatchOperationType.RENAME_SHEET],
        cells_written=counters[PatchOperationType.WRITE_RANGE],
        cells_cleared=counters[PatchOperationType.CLEAR_RANGE],
        formulas_written=formulas,
        formats_changed=counters[PatchOperationType.SET_NUMBER_FORMAT],
    )


__all__ = [
    "PATCH_COMPILER_VERSION",
    "PATCH_SCHEMA_VERSION",
    "PatchImpact",
    "PatchStatus",
    "WorkbookGuard",
    "WorkbookPatch",
    "canonical_patch_payload",
    "compute_patch_hash",
    "summarize_impact",
]
