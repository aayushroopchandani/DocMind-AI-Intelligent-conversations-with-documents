"""Verifying that the browser applied what it said it applied (Phase 9.12.4).

The workbook is local, so the server cannot read it back and check. What it can
do is refuse to accept any claim it did not already compute the answer to: every
hash in a receipt was produced by this server when it compiled the patch, so a
receipt is either arithmetically consistent with that patch or it is rejected.

Two failure modes get named treatment, because getting them wrong is expensive
in opposite directions:

*A re-delivered receipt is not a second application.* If the local apply
succeeded but the network call did not, the client retries the identical
receipt. That must be accepted as the same application, or the user is asked to
apply an edit that already happened.

*A partial application is not a success.* If any operation did not apply, the
run does not complete — 9.12's acceptance criteria say a conflict never
partially applies the rest, and a receipt admitting partial state is a rollback
signal, not a completion.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .envelope import PatchStatus, WorkbookPatch


if TYPE_CHECKING:  # The durable record imports this module, not the reverse.
    from ..models.patches import PatchProposal


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OperationOutcome(str, Enum):
    APPLIED = "applied"
    SKIPPED = "skipped"
    FAILED = "failed"


class OperationResult(BaseModel):
    """What the adapter reports for one operation."""

    op_id: str = Field(min_length=1, max_length=120)
    outcome: OperationOutcome
    affected_cells: int = Field(default=0, ge=0)
    after_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    message: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class TouchedRange(BaseModel):
    """One rectangle the adapter says it changed, and what it holds now."""

    worksheet_id: str = Field(min_length=1, max_length=200)
    range_a1: str = Field(min_length=5, max_length=100)
    after_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class PatchApplicationReceipt(BaseModel):
    """The client's authenticated statement that it applied a patch (9.12.4).

    The workbook is local, so the server is not re-reading it to check. It is
    verifying a receipt whose every claim is bound to hashes the server itself
    computed, over a rectangle whose size it already knows. A receipt that
    disagrees with any of them is rejected, and the run does not complete.
    """

    application_id: str = Field(min_length=8, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=200)

    patch_id: str = Field(min_length=1, max_length=120)
    patch_revision: int = Field(ge=1)
    patch_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_id: str = Field(min_length=1, max_length=120)

    base_revision: int = Field(ge=0)
    applied_revision: int = Field(ge=1)

    adapter_version: str = Field(min_length=1, max_length=60)
    engine_version: str = Field(min_length=1, max_length=60)

    operation_results: tuple[OperationResult, ...] = Field(
        min_length=1,
        max_length=200,
    )
    touched_ranges: tuple[TouchedRange, ...] = Field(default=(), max_length=200)
    pre_application_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    post_application_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    locally_persisted: bool = False
    applied_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if self.applied_revision <= self.base_revision:
            raise ValueError("applied_revision must advance past base_revision")
        op_ids = [item.op_id for item in self.operation_results]
        if len(op_ids) != len(set(op_ids)):
            raise ValueError("operation results must be unique")
        return self

    @property
    def fully_applied(self) -> bool:
        return all(
            item.outcome is OperationOutcome.APPLIED
            for item in self.operation_results
        )

    @property
    def touched_range_hash(self) -> str:
        return touched_range_hash(self.touched_ranges)


def touched_range_hash(ranges: tuple[TouchedRange, ...]) -> str:
    """Return one digest over the bounded evidence the client reported."""

    payload = sorted(
        (item.worksheet_id, item.range_a1, item.after_hash) for item in ranges
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()




@dataclass(frozen=True, slots=True)
class ReceiptVerdict:
    """Whether the receipt may be recorded, and why not if it may not."""

    accepted: bool
    code: str | None = None
    message: str | None = None
    duplicate: bool = False
    partial: bool = False

    @classmethod
    def ok(cls, *, duplicate: bool = False) -> ReceiptVerdict:
        return cls(accepted=True, duplicate=duplicate)


def expected_pre_hash(patch: WorkbookPatch) -> str:
    """Return the digest of every touched rectangle as it must be before apply."""

    return touched_range_hash(_touched(patch, after=False))


def expected_post_hash(patch: WorkbookPatch) -> str:
    """Return the digest of every touched rectangle as it must be after apply."""

    return touched_range_hash(_touched(patch, after=True))


def verify_receipt(
    receipt: PatchApplicationReceipt,
    *,
    proposal: PatchProposal,
    now: datetime | None = None,
) -> ReceiptVerdict:
    """Return whether `receipt` truthfully describes applying `proposal`."""

    if proposal.application is not None:
        return _duplicate(receipt, proposal)

    if proposal.status is not PatchStatus.APPROVED:
        return ReceiptVerdict(
            accepted=False,
            code="patch_not_approved",
            message=(
                f"a patch in status '{proposal.status.value}' cannot be applied"
            ),
        )
    if not proposal.approval.is_usable(now=now):
        return ReceiptVerdict(
            accepted=False,
            code="patch_approval_expired",
            message="the approval for this patch has expired",
        )

    binding = proposal.binding
    if (
        receipt.patch_id != binding.patch_id
        or receipt.patch_revision != binding.patch_revision
        or receipt.patch_hash != binding.patch_hash
        or receipt.plan_hash != binding.plan_hash
        or receipt.base_revision != binding.base_workbook_revision
    ):
        return ReceiptVerdict(
            accepted=False,
            code="patch_binding_mismatch",
            message="the receipt does not match the approved patch",
        )
    if receipt.execution_id != proposal.execution_id:
        return ReceiptVerdict(
            accepted=False,
            code="patch_binding_mismatch",
            message="the receipt names a different execution",
        )
    if receipt.applied_revision != receipt.base_revision + 1:
        return ReceiptVerdict(
            accepted=False,
            code="workbook_revision_skipped",
            message=(
                "one patch advances the workbook by exactly one revision; the "
                f"receipt reports {receipt.base_revision} to "
                f"{receipt.applied_revision}"
            ),
        )

    coverage = _coverage_error(receipt, proposal.patch)
    if coverage is not None:
        return coverage
    if not receipt.fully_applied:
        return ReceiptVerdict(
            accepted=False,
            code="patch_partially_applied",
            message=(
                "some operations did not apply; the workbook must be rolled "
                "back through the stored inverse rather than continued"
            ),
            partial=True,
        )

    if receipt.pre_application_hash != expected_pre_hash(proposal.patch):
        return ReceiptVerdict(
            accepted=False,
            code="pre_application_hash_mismatch",
            message="the reported pre-application state is not what the patch expected",
        )
    if receipt.post_application_hash != expected_post_hash(proposal.patch):
        return ReceiptVerdict(
            accepted=False,
            code="post_application_hash_mismatch",
            message="the reported result is not what the patch would produce",
        )
    if receipt.touched_ranges:
        if receipt.touched_range_hash != receipt.post_application_hash:
            return ReceiptVerdict(
                accepted=False,
                code="touched_range_mismatch",
                message="the reported touched ranges disagree with the result hash",
            )
    return ReceiptVerdict.ok()


def _duplicate(
    receipt: PatchApplicationReceipt,
    proposal: PatchProposal,
) -> ReceiptVerdict:
    stored = proposal.application
    assert stored is not None
    same = (
        stored.idempotency_key == receipt.idempotency_key
        and stored.patch_hash == receipt.patch_hash
        and stored.applied_revision == receipt.applied_revision
    )
    if same:
        # The edit happened; only its receipt was lost. Recording it again is
        # a no-op, and telling the client so stops it re-applying.
        return ReceiptVerdict.ok(duplicate=True)
    return ReceiptVerdict(
        accepted=False,
        code="patch_already_applied",
        message="this patch was already applied by a different receipt",
    )


def _coverage_error(
    receipt: PatchApplicationReceipt,
    patch: WorkbookPatch,
) -> ReceiptVerdict | None:
    reported = {item.op_id: item for item in receipt.operation_results}
    expected = {operation.op_id for operation in patch.operations}
    missing = expected.difference(reported)
    if missing:
        return ReceiptVerdict(
            accepted=False,
            code="patch_partially_applied",
            message=(
                "the receipt does not account for every operation: "
                + ", ".join(sorted(missing))
            ),
            partial=True,
        )
    unknown = set(reported).difference(expected)
    if unknown:
        return ReceiptVerdict(
            accepted=False,
            code="unknown_patch_operation",
            message=(
                "the receipt reports operations this patch does not contain: "
                + ", ".join(sorted(unknown))
            ),
        )
    for operation in patch.operations:
        result = reported[operation.op_id]
        if result.outcome is not OperationOutcome.APPLIED:
            continue
        if (
            operation.expected_after_hash is not None
            and result.after_hash != operation.expected_after_hash
        ):
            return ReceiptVerdict(
                accepted=False,
                code="post_application_hash_mismatch",
                message=(
                    f"operation '{operation.op_id}' reports a result the patch "
                    "does not describe"
                ),
            )
    return None


def _touched(patch: WorkbookPatch, *, after: bool) -> tuple[TouchedRange, ...]:
    """Return the rectangles a patch changes, with their before/after hashes."""

    entries: list[TouchedRange] = []
    for operation in patch.operations:
        expected = (
            operation.expected_after_hash if after else operation.expected_before_hash
        )
        if operation.range_a1 is None or expected is None:
            continue
        entries.append(
            TouchedRange(
                worksheet_id=operation.worksheet_id,
                range_a1=operation.range_a1,
                after_hash=expected,
            )
        )
    return tuple(entries)


__all__ = [
    "OperationOutcome",
    "OperationResult",
    "PatchApplicationReceipt",
    "ReceiptVerdict",
    "TouchedRange",
    "expected_post_hash",
    "expected_pre_hash",
    "touched_range_hash",
    "verify_receipt",
]
