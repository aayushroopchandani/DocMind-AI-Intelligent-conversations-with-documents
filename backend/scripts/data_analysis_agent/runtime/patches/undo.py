"""Undoing an applied patch, later, as its own auditable action (Phase 9.12.6).

There are two undos in Phase 9 and only one of them lives here. The immediate
one is the editor's: a single Ctrl/Cmd+Z reverses the whole patch because the
adapter applied it as one logical command. That needs no server.

This is the durable one. Minutes or days after the fact, the stored inverse can
be proposed as a *new* patch — conflict-checked against the workbook as it is
now, approved on its own terms, and applied with its own receipt. It is not a
database rollback and it does not rewrite history: the original application
record stands, and the undo is a second record linked to it.

Which is why the undo carries the original's operations as *its* inverse. Undo
is reversible too.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from .envelope import (
    PatchStatus,
    WorkbookGuard,
    WorkbookPatch,
    compute_patch_hash,
    summarize_impact,
)


if TYPE_CHECKING:  # The durable record imports this package, not the reverse.
    from ..models.patches import PatchProposal


class UndoNotAvailableError(RuntimeError):
    """This patch cannot be undone through the durable path."""


def build_undo_patch(
    proposal: PatchProposal,
    *,
    patch_id: str,
    idempotency_key: str,
    workbook_revision: int,
    expires_at: datetime | None = None,
) -> WorkbookPatch:
    """Return a new patch that reverses `proposal`, ready for approval."""

    if proposal.status is not PatchStatus.APPLIED:
        raise UndoNotAvailableError(
            f"a patch in status '{proposal.status.value}' has not been applied"
        )
    original = proposal.patch
    if not original.inverse_operations:
        raise UndoNotAvailableError(
            "this patch was applied without a stored inverse and cannot be "
            "undone automatically"
        )

    operations = original.inverse_operations
    impact = summarize_impact(operations).model_copy(
        update={
            # Undo removes the patch's own output, which is existing content by
            # the time this runs. Declaring that keeps the reversibility rule
            # honest rather than sidestepping it.
            "overwrites_existing_values": True,
            "overwrites_existing_formulas": bool(
                original.impact.formulas_written
            ),
        }
    )
    guards = tuple(
        WorkbookGuard(
            worksheet_id=operation.worksheet_id,
            range_a1=operation.range_a1,
            expected_hash=operation.expected_before_hash,
            role="target",
        )
        for operation in operations
        if operation.range_a1 is not None
        and operation.expected_before_hash is not None
    )
    draft = WorkbookPatch(
        patch_id=patch_id,
        patch_revision=1,
        patch_hash="0" * 64,
        user_id=original.user_id,
        workspace_id=original.workspace_id,
        run_id=original.run_id,
        plan_id=original.plan_id,
        plan_hash=original.plan_hash,
        execution_id=original.execution_id,
        workbook_id=original.workbook_id,
        base_workbook_revision=workbook_revision,
        source_guards=(),
        target_guards=guards,
        operations=operations,
        # Undoing the undo restores the patch's output, so the round trip is
        # closed rather than one-way.
        inverse_operations=original.operations,
        impact=impact,
        maximum_affected_cells=original.maximum_affected_cells,
        idempotency_key=idempotency_key,
        compiler_version=original.compiler_version,
        cell_hash_version=original.cell_hash_version,
        status=PatchStatus.DRAFT,
        expires_at=expires_at,
    )
    return draft.model_copy(update={"patch_hash": compute_patch_hash(draft)})


__all__ = [
    "UndoNotAvailableError",
    "build_undo_patch",
]
