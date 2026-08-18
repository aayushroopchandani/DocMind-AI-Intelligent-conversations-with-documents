"""Durable state around one workbook patch (Phase 9.12).

`WorkbookPatch` is the protocol document — what the user approves and the
adapter applies. This module is everything the server keeps *around* it: who
approved it and against exactly which hashes, what the browser reported after
applying it, which patch it supersedes or undoes, and which rectangle it holds.

The split matters. The patch is immutable and hashed; if approval or application
state lived inside it, every lifecycle change would change its identity and no
approval could survive its own recording.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..patches.envelope import PatchStatus, WorkbookPatch
from ..patches.preview import PatchPreview
from ..patches.receipt import PatchApplicationReceipt


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PatchDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PatchRejectionReason(str, Enum):
    WRONG_TARGET = "wrong_target"
    WRONG_RESULT = "wrong_result"
    TOO_INVASIVE = "too_invasive"
    NOT_NEEDED = "not_needed"
    OTHER = "other"


class PatchBinding(BaseModel):
    """What an approval is bound to (9.12.1).

    Change any one of these and the approval is void. That is the whole point:
    a user approved *this* change to *this* workbook at *this* revision, and a
    recompiled patch has to be approved again.
    """

    patch_id: str = Field(min_length=1, max_length=120)
    patch_revision: int = Field(ge=1)
    patch_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_workbook_revision: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True)

    def matches(self, other: PatchBinding) -> bool:
        return self == other


class PatchApproval(BaseModel):
    """The human decision, and the exact thing it was a decision about."""

    status: PatchDecision = PatchDecision.PENDING
    binding: PatchBinding | None = None
    decision_id: str | None = Field(default=None, min_length=8, max_length=200)
    decided_at: datetime | None = None
    comment: str | None = Field(default=None, max_length=1_000)
    rejection_reason: PatchRejectionReason | None = None
    expires_at: datetime | None = None

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        decided = self.status is not PatchDecision.PENDING
        if decided and (self.binding is None or self.decision_id is None):
            raise ValueError("a decided patch records its binding and decision ID")
        if self.status is PatchDecision.APPROVED and self.expires_at is None:
            raise ValueError("an approved patch must carry an expiry")
        if (
            self.rejection_reason is not None
            and self.status is not PatchDecision.REJECTED
        ):
            raise ValueError("only a rejection carries a rejection reason")
        return self

    def is_usable(self, *, now: datetime | None = None) -> bool:
        if self.status is not PatchDecision.APPROVED or self.expires_at is None:
            return False
        moment = now or utc_now()
        expiry = (
            self.expires_at
            if self.expires_at.tzinfo is not None
            else self.expires_at.replace(tzinfo=timezone.utc)
        )
        return expiry > moment


class PatchApprovalCommand(BaseModel):
    """A tenant-scoped, idempotent approve/reject request."""

    decision: Literal["approve", "reject"]
    binding: PatchBinding
    decision_id: str = Field(min_length=8, max_length=200)
    comment: str | None = Field(default=None, max_length=1_000)
    rejection_reason: PatchRejectionReason | None = None

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_command(self) -> Self:
        if self.decision == "approve" and self.rejection_reason is not None:
            raise ValueError("an approval cannot carry a rejection reason")
        return self


class PatchPlacementSummary(BaseModel):
    """Where the patch landed and why, in words a reviewer can read."""

    worksheet_id: str = Field(min_length=1, max_length=200)
    worksheet_name: str = Field(min_length=1, max_length=255)
    target_range_a1: str = Field(min_length=5, max_length=100)
    policy: str = Field(min_length=1, max_length=40)
    creates_sheet: bool = False
    overwrites: bool = False
    relocated: bool = False
    explanation: str = Field(min_length=1, max_length=500)
    collision_codes: tuple[str, ...] = Field(default=(), max_length=20)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class PatchProposal(BaseModel):
    """One patch and everything durable about its life."""

    schema_version: int = 1
    patch_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    user_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=36, max_length=36)
    plan_id: str = Field(min_length=36, max_length=36)
    execution_id: str = Field(min_length=1, max_length=120)

    patch: WorkbookPatch
    placement: PatchPlacementSummary
    preview: PatchPreview | None = None

    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reservation_id: str | None = Field(default=None, max_length=120)

    status: PatchStatus = PatchStatus.AWAITING_APPROVAL
    approval: PatchApproval = Field(default_factory=PatchApproval)
    application: PatchApplicationReceipt | None = None

    #: Set when this patch replaces an earlier one — a rebase or a relocation.
    supersedes_patch_id: str | None = Field(default=None, max_length=120)
    #: Set when this patch is the durable undo of an applied one (9.12.6).
    undoes_patch_id: str | None = Field(default=None, max_length=120)

    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_proposal(self) -> Self:
        if self.patch.patch_id != self.patch_id:
            raise ValueError("proposal and patch IDs must agree")
        if self.patch.patch_revision != self.revision:
            raise ValueError("proposal and patch revisions must agree")
        if self.patch.user_id != self.user_id:
            raise ValueError("proposal and patch owners must agree")
        if self.patch.workspace_id != self.workspace_id:
            raise ValueError("proposal and patch workspaces must agree")
        if self.patch.run_id != self.run_id:
            raise ValueError("proposal and patch runs must agree")
        expected = {
            PatchDecision.PENDING: PatchStatus.AWAITING_APPROVAL,
            PatchDecision.APPROVED: PatchStatus.APPROVED,
            PatchDecision.REJECTED: PatchStatus.REJECTED,
        }[self.approval.status]
        allowed = {
            expected,
            PatchStatus.EXPIRED,
            PatchStatus.APPLIED,
            PatchStatus.SUPERSEDED,
        }
        if self.status not in allowed:
            raise ValueError("patch status and approval status disagree")
        if self.status is PatchStatus.APPLIED and self.application is None:
            raise ValueError("an applied patch records its receipt")
        if self.application is not None and self.application.patch_id != self.patch_id:
            raise ValueError("the receipt belongs to a different patch")
        return self

    @property
    def binding(self) -> PatchBinding:
        return PatchBinding(
            patch_id=self.patch_id,
            patch_revision=self.revision,
            patch_hash=self.patch.patch_hash,
            plan_hash=self.patch.plan_hash,
            base_workbook_revision=self.patch.base_workbook_revision,
        )

    @property
    def is_open(self) -> bool:
        return self.status in {
            PatchStatus.DRAFT,
            PatchStatus.AWAITING_APPROVAL,
            PatchStatus.APPROVED,
        }


__all__ = [
    "PatchApproval",
    "PatchApprovalCommand",
    "PatchBinding",
    "PatchDecision",
    "PatchPlacementSummary",
    "PatchProposal",
    "PatchRejectionReason",
    "utc_now",
]
