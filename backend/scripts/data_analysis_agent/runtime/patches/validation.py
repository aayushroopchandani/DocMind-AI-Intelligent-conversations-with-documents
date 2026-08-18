"""What must be true before a patch may be shown to a user or applied.

Three separable questions, answered in order:

1. *Is every operation one this deployment can actually apply?* A reserved type
   fails here with `unsupported_patch_operation` rather than reaching an adapter
   that has no code for it.
2. *Is the patch internally consistent?* Hash matches content, impact matches
   operations, dependencies resolve, nothing exceeds its declared bound.
3. *Does it still match the workbook?* Guard hashes against the live context —
   which is the only check the backend cannot perform alone, because the
   workbook lives in the browser.

Duplicate application is detectable at step 3: an already-applied patch no
longer matches its own `expected_before_hash`, so a second attempt is refused
before it mutates anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cells import CellState, range_hash
from .envelope import PatchStatus, WorkbookPatch, compute_patch_hash, summarize_impact
from .operations import (
    PatchOperation,
    RESERVED_OPERATIONS,
    SUPPORTED_OPERATIONS,
)


@dataclass(frozen=True, slots=True)
class PatchIssue:
    code: str
    message: str
    op_id: str | None = None
    #: (worksheet_id, range_a1) for guard failures, so a caller can tell a
    #: changed source apart from an occupied target without parsing messages.
    range_key: tuple[str, str] | None = None


def validate_patch(patch: WorkbookPatch) -> tuple[PatchIssue, ...]:
    """Check a patch against itself, with no workbook access."""

    issues: list[PatchIssue] = []
    issues.extend(_operation_support(patch))
    issues.extend(_integrity(patch))
    return tuple(issues)


def _operation_support(patch: WorkbookPatch) -> tuple[PatchIssue, ...]:
    issues: list[PatchIssue] = []
    for operation in (*patch.operations, *patch.inverse_operations):
        if operation.operation_type in SUPPORTED_OPERATIONS:
            continue
        reserved = operation.operation_type in RESERVED_OPERATIONS
        issues.append(
            PatchIssue(
                code="unsupported_patch_operation",
                message=(
                    f"'{operation.operation_type.value}' is reserved in the "
                    "protocol but has no verified adapter yet"
                    if reserved
                    else f"'{operation.operation_type.value}' is not applicable"
                ),
                op_id=operation.op_id,
            )
        )
    return tuple(issues)


def _integrity(patch: WorkbookPatch) -> tuple[PatchIssue, ...]:
    issues: list[PatchIssue] = []
    if patch.patch_hash != compute_patch_hash(patch):
        issues.append(
            PatchIssue(
                code="patch_hash_mismatch",
                message="patch_hash does not match the canonical patch content",
            )
        )
    derived = summarize_impact(patch.operations)
    if derived.total_cells != patch.impact.total_cells:
        issues.append(
            PatchIssue(
                code="patch_impact_mismatch",
                message=(
                    f"declared impact of {patch.impact.total_cells} cells does "
                    f"not match the {derived.total_cells} the operations affect"
                ),
            )
        )
    if patch.affected_cells > patch.maximum_affected_cells:
        issues.append(
            PatchIssue(
                code="patch_too_large",
                message=(
                    f"patch affects {patch.affected_cells} cells, above its "
                    f"declared maximum of {patch.maximum_affected_cells}"
                ),
            )
        )
    destructive = patch.impact.overwrites_existing_values or (
        patch.impact.overwrites_existing_formulas
    )
    if destructive and not patch.is_reversible:
        issues.append(
            PatchIssue(
                code="patch_not_reversible",
                message="a destructive patch must carry an inverse",
            )
        )
    return tuple(issues)


def check_guards(
    patch: WorkbookPatch,
    *,
    live: dict[tuple[str, str], tuple[tuple[CellState, ...], ...]],
    workbook_revision: int,
) -> tuple[PatchIssue, ...]:
    """Check a patch against the live workbook context.

    `live` maps (worksheet_id, range_a1) to the cells the client just captured.
    A guard whose rectangle is absent is a failure, not a pass — the backend
    must never assume an uncaptured rectangle is empty.
    """

    issues: list[PatchIssue] = []
    if workbook_revision != patch.base_workbook_revision:
        issues.append(
            PatchIssue(
                code="workbook_revision_changed",
                message=(
                    f"patch was compiled against revision "
                    f"{patch.base_workbook_revision}; the workbook is now at "
                    f"{workbook_revision}"
                ),
            )
        )
    for guard in (*patch.source_guards, *patch.target_guards):
        key = (guard.worksheet_id, guard.range_a1)
        cells = live.get(key)
        if cells is None:
            issues.append(
                PatchIssue(
                    code="guard_context_missing",
                    message=(
                        f"no captured context for {guard.role} range "
                        f"{guard.range_a1}"
                    ),
                    range_key=key,
                )
            )
            continue
        if range_hash(guard.range_a1, cells) != guard.expected_hash:
            issues.append(
                PatchIssue(
                    code="guard_hash_mismatch",
                    message=(
                        f"{guard.role} range {guard.range_a1} changed since the "
                        "patch was compiled"
                    ),
                    range_key=key,
                )
            )
    return tuple(issues)


def is_already_applied(
    patch: WorkbookPatch,
    *,
    live: dict[tuple[str, str], tuple[tuple[CellState, ...], ...]],
) -> bool:
    """Return whether every operation's target already holds its result.

    Used to make duplicate delivery detectable *before* mutation: if each
    target already matches `expected_after_hash`, the patch has been applied and
    re-applying it would be a no-op at best.
    """

    checked = 0
    for operation in patch.operations:
        if operation.range_a1 is None or operation.expected_after_hash is None:
            continue
        cells = live.get((operation.worksheet_id, operation.range_a1))
        if cells is None:
            return False
        if range_hash(operation.range_a1, cells) != operation.expected_after_hash:
            return False
        checked += 1
    return checked > 0


def applicable_status(patch: WorkbookPatch) -> tuple[PatchIssue, ...]:
    """Check the lifecycle allows application right now."""

    if patch.status is not PatchStatus.APPROVED:
        return (
            PatchIssue(
                code="patch_not_approved",
                message=f"a patch in status '{patch.status.value}' cannot apply",
            ),
        )
    return ()


def operation_order(patch: WorkbookPatch) -> tuple[PatchOperation, ...]:
    """Return operations in a dependency-respecting order."""

    remaining = list(patch.operations)
    done: set[str] = set()
    ordered: list[PatchOperation] = []
    while remaining:
        ready = [
            operation
            for operation in remaining
            if set(operation.depends_on).issubset(done)
        ]
        if not ready:
            blocked = ", ".join(sorted(item.op_id for item in remaining))
            raise ValueError(f"patch operations cannot be ordered: {blocked}")
        for operation in ready:
            ordered.append(operation)
            done.add(operation.op_id)
            remaining.remove(operation)
    return tuple(ordered)


__all__ = [
    "PatchIssue",
    "applicable_status",
    "check_guards",
    "is_already_applied",
    "operation_order",
    "validate_patch",
]
