"""The conflict matrix, and the rebase that avoids most of it (Phase 9.12.5).

A patch is compiled against one view of a workbook and applied against another,
because a human read it in between. Every way those two views can differ has
exactly one safe response, and this module is that table — written once, so no
call site gets to invent a sixth answer.

The important entry is the first. A workbook whose revision moved but whose
source, target and structure hashes all still match has not changed in any way
this patch depends on; rebasing onto the new revision is deterministic
arithmetic, not a judgement call, and needs no LLM. Everything else is
conservative: re-plan, relocate, ask, or recover — never continue.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .cells import CellState
from .envelope import PatchStatus, WorkbookPatch, compute_patch_hash
from .validation import PatchIssue, check_guards, is_already_applied


class ConflictKind(str, Enum):
    NONE = "none"
    REVISION_ADVANCED = "revision_advanced"
    SOURCE_CHANGED = "source_changed"
    TARGET_OCCUPIED = "target_occupied"
    WORKBOOK_MISSING = "workbook_missing"
    ALREADY_APPLIED = "already_applied"
    PARTIAL_STATE = "partial_state"


class ConflictResolution(str, Enum):
    PROCEED = "proceed"
    REBASE = "rebase"
    REPLAN = "replan"
    RELOCATE = "relocate"
    REQUEST_TARGET = "request_target"
    RECOVER_RECEIPT = "recover_receipt"
    ROLL_BACK = "roll_back"


#: The matrix itself. Reading it is the fastest way to understand this module.
CONFLICT_MATRIX: dict[ConflictKind, ConflictResolution] = {
    ConflictKind.NONE: ConflictResolution.PROCEED,
    ConflictKind.REVISION_ADVANCED: ConflictResolution.REBASE,
    ConflictKind.SOURCE_CHANGED: ConflictResolution.REPLAN,
    ConflictKind.TARGET_OCCUPIED: ConflictResolution.RELOCATE,
    ConflictKind.WORKBOOK_MISSING: ConflictResolution.REQUEST_TARGET,
    ConflictKind.ALREADY_APPLIED: ConflictResolution.RECOVER_RECEIPT,
    ConflictKind.PARTIAL_STATE: ConflictResolution.ROLL_BACK,
}


@dataclass(frozen=True, slots=True)
class ConflictAssessment:
    """What is wrong, what to do about it, and what the user should be told."""

    kind: ConflictKind
    resolution: ConflictResolution
    message: str
    issues: tuple[PatchIssue, ...] = ()

    @property
    def blocks_application(self) -> bool:
        return self.resolution is not ConflictResolution.PROCEED


def assess_conflict(
    patch: WorkbookPatch,
    *,
    live: dict[tuple[str, str], tuple[tuple[CellState, ...], ...]],
    workbook_revision: int,
    workbook_present: bool = True,
) -> ConflictAssessment:
    """Classify the difference between the patch's world and the live one."""

    if not workbook_present:
        return _assessment(
            ConflictKind.WORKBOOK_MISSING,
            "the workbook or worksheet this patch targets no longer exists",
        )
    if is_already_applied(patch, live=live):
        return _assessment(
            ConflictKind.ALREADY_APPLIED,
            "this patch has already been applied; recovering its receipt",
        )

    issues = check_guards(
        patch,
        live=live,
        workbook_revision=workbook_revision,
    )
    if not issues:
        return _assessment(ConflictKind.NONE, "the workbook still matches")

    source_ranges = {
        (guard.worksheet_id, guard.range_a1) for guard in patch.source_guards
    }
    guard_failures = tuple(
        issue
        for issue in issues
        if issue.code in {"guard_hash_mismatch", "guard_context_missing"}
    )
    revision_moved = any(
        issue.code == "workbook_revision_changed" for issue in issues
    )

    if not guard_failures and revision_moved:
        # Nothing the patch depends on moved; only the counter did.
        return _assessment(
            ConflictKind.REVISION_ADVANCED,
            (
                f"the workbook advanced to revision {workbook_revision}; "
                "the patch can be rebased without changing what it does"
            ),
            issues,
        )

    source_changed = any(
        issue.range_key in source_ranges for issue in guard_failures
    )
    if source_changed:
        return _assessment(
            ConflictKind.SOURCE_CHANGED,
            (
                "the data this result was computed from has changed; the "
                "analysis has to run again"
            ),
            issues,
        )
    return _assessment(
        ConflictKind.TARGET_OCCUPIED,
        "the target range is no longer free; the result needs a new home",
        issues,
    )


def rebase_patch(
    patch: WorkbookPatch,
    *,
    workbook_revision: int,
) -> WorkbookPatch:
    """Return the same change, bound to a newer workbook revision.

    Deterministic by construction: only the base revision and the patch revision
    move, so the operations, payloads and guards are byte-identical. The hash
    changes because the binding changed, which is exactly why the old approval
    cannot be reused (9.12.1).
    """

    if workbook_revision <= patch.base_workbook_revision:
        raise ValueError(
            f"rebase target revision {workbook_revision} does not advance past "
            f"{patch.base_workbook_revision}"
        )
    draft = patch.model_copy(
        update={
            "base_workbook_revision": workbook_revision,
            "patch_revision": patch.patch_revision + 1,
            "status": PatchStatus.DRAFT,
            "expires_at": None,
            "patch_hash": "0" * 64,
        }
    )
    return draft.model_copy(update={"patch_hash": compute_patch_hash(draft)})


def _assessment(
    kind: ConflictKind,
    message: str,
    issues: tuple[PatchIssue, ...] = (),
) -> ConflictAssessment:
    return ConflictAssessment(
        kind=kind,
        resolution=CONFLICT_MATRIX[kind],
        message=message,
        issues=issues,
    )


__all__ = [
    "CONFLICT_MATRIX",
    "ConflictAssessment",
    "ConflictKind",
    "ConflictResolution",
    "assess_conflict",
    "rebase_patch",
]
