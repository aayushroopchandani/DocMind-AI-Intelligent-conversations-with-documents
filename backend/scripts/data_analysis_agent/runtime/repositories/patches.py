"""Durable storage for patch proposals (Phase 9.12).

Three guarantees, and each one is a compare-and-set rather than a read followed
by a write:

*One decision per patch revision.* Approval is conditional on the exact binding
the user saw — patch ID, revision, patch hash, plan hash and base workbook
revision. A patch that was recompiled in the meantime has a different binding, so
the stale approval simply does not match and cannot be replayed (9.12.1).

*One application per patch.* Recording a receipt requires the proposal to still
be approved and unapplied. A duplicate receipt therefore cannot produce a second
application record, which is what makes lost-receipt retries safe (9.12.4).

*One live revision per run.* Creating a new revision supersedes the open ones in
the same transaction, so a rebase cannot leave two patches both waiting for
approval.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from db.mongodb import get_db

from ..models.patches import (
    PatchApplicationReceipt,
    PatchApproval,
    PatchApprovalCommand,
    PatchDecision,
    PatchProposal,
    utc_now,
)
from ..patches.envelope import PatchStatus


_OPEN_STATUSES = (
    PatchStatus.DRAFT.value,
    PatchStatus.AWAITING_APPROVAL.value,
    PatchStatus.APPROVED.value,
)


class PatchRepositoryError(RuntimeError):
    """Patch persistence failed."""


class PatchNotFoundError(PatchRepositoryError):
    """The tenant-scoped patch proposal does not exist."""


class PatchConflictError(PatchRepositoryError):
    """The proposal changed under this caller; its write was rejected."""


def _as_utc(value: datetime) -> datetime:
    return (
        value.astimezone(timezone.utc)
        if value.tzinfo is not None
        else value.replace(tzinfo=timezone.utc)
    )


class PatchProposalRepository(Protocol):
    async def create(self, proposal: PatchProposal) -> PatchProposal: ...

    async def get(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
        revision: int | None = None,
    ) -> PatchProposal | None: ...

    async def get_current(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> PatchProposal | None: ...

    async def decide(
        self,
        *,
        user_id: str,
        run_id: str,
        command: PatchApprovalCommand,
        decided_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> PatchProposal: ...

    async def record_application(
        self,
        *,
        user_id: str,
        run_id: str,
        receipt: PatchApplicationReceipt,
    ) -> PatchProposal: ...

    async def mark_status(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
        revision: int,
        status: PatchStatus,
    ) -> PatchProposal: ...


def _approval_for(
    command: PatchApprovalCommand,
    *,
    decided_at: datetime,
    expires_at: datetime | None,
) -> PatchApproval:
    approved = command.decision == "approve"
    return PatchApproval(
        status=PatchDecision.APPROVED if approved else PatchDecision.REJECTED,
        binding=command.binding,
        decision_id=command.decision_id,
        decided_at=decided_at,
        comment=command.comment,
        rejection_reason=command.rejection_reason,
        expires_at=expires_at if approved else None,
    )


def _decided_status(command: PatchApprovalCommand) -> PatchStatus:
    return (
        PatchStatus.APPROVED
        if command.decision == "approve"
        else PatchStatus.REJECTED
    )


class MongoPatchProposalRepository:
    collection_name = "analysis_patch_proposals"

    def __init__(self, database: Any | None = None) -> None:
        self._database = database

    def _db(self) -> Any:
        return self._database if self._database is not None else get_db()

    async def _in_transaction(
        self,
        callback: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        try:
            session_context = await self._db().client.start_session()
            async with session_context as session:
                return await session.with_transaction(callback)
        except PatchRepositoryError:
            raise
        except PyMongoError as error:
            raise PatchRepositoryError(
                "patch transaction failed; MongoDB transaction support is "
                "required"
            ) from error

    async def create(self, proposal: PatchProposal) -> PatchProposal:
        collection = self._db()[self.collection_name]

        async def transaction(session: Any) -> PatchProposal:
            existing = await collection.find_one(
                {
                    "user_id": proposal.user_id,
                    "run_id": proposal.run_id,
                    "patch_id": proposal.patch_id,
                    "revision": proposal.revision,
                },
                session=session,
            )
            if existing is not None:
                current = PatchProposal.model_validate(existing)
                if current.patch.patch_hash == proposal.patch.patch_hash:
                    return current
                raise PatchConflictError(
                    "a different patch already occupies this revision"
                )
            # Exactly one open proposal per run: the new revision replaces
            # whatever was waiting, in the same transaction that creates it.
            await collection.update_many(
                {
                    "user_id": proposal.user_id,
                    "run_id": proposal.run_id,
                    "status": {"$in": list(_OPEN_STATUSES)},
                },
                {
                    "$set": {
                        "status": PatchStatus.SUPERSEDED.value,
                        "updated_at": proposal.created_at,
                    }
                },
                session=session,
            )
            await collection.insert_one(
                proposal.model_dump(mode="python"),
                session=session,
            )
            return proposal

        try:
            return await self._in_transaction(transaction)
        except DuplicateKeyError as error:  # pragma: no cover - index guards it
            raise PatchConflictError(
                "this patch revision already exists"
            ) from error

    async def get(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
        revision: int | None = None,
    ) -> PatchProposal | None:
        query: dict[str, Any] = {
            "user_id": user_id,
            "run_id": run_id,
            "patch_id": patch_id,
        }
        if revision is not None:
            query["revision"] = revision
        try:
            documents = await (
                self._db()[self.collection_name]
                .find(query)
                .sort([("revision", -1)])
                .limit(1)
                .to_list(length=1)
            )
        except PyMongoError as error:
            raise PatchRepositoryError("patch could not be read") from error
        return PatchProposal.model_validate(documents[0]) if documents else None

    async def get_current(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> PatchProposal | None:
        try:
            documents = await (
                self._db()[self.collection_name]
                .find({"user_id": user_id, "run_id": run_id})
                .sort([("created_at", -1), ("revision", -1)])
                .limit(1)
                .to_list(length=1)
            )
        except PyMongoError as error:
            raise PatchRepositoryError("patch could not be read") from error
        return PatchProposal.model_validate(documents[0]) if documents else None

    async def decide(
        self,
        *,
        user_id: str,
        run_id: str,
        command: PatchApprovalCommand,
        decided_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> PatchProposal:
        moment = _as_utc(decided_at or utc_now())
        binding = command.binding
        approval = _approval_for(
            command,
            decided_at=moment,
            expires_at=expires_at,
        )
        try:
            document = await self._db()[
                self.collection_name
            ].find_one_and_update(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "patch_id": binding.patch_id,
                    "revision": binding.patch_revision,
                    "patch.patch_hash": binding.patch_hash,
                    "patch.plan_hash": binding.plan_hash,
                    "patch.base_workbook_revision": (
                        binding.base_workbook_revision
                    ),
                    "$or": [
                        {"status": PatchStatus.AWAITING_APPROVAL.value},
                        # An identical decision replayed is the same decision.
                        {"approval.decision_id": command.decision_id},
                    ],
                },
                {
                    "$set": {
                        "status": _decided_status(command).value,
                        "approval": approval.model_dump(mode="python"),
                        "updated_at": moment,
                    },
                    "$inc": {"version": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as error:
            raise PatchRepositoryError("patch decision failed") from error
        if document is None:
            raise PatchConflictError(
                "the patch changed since it was shown; approve the new one"
            )
        return PatchProposal.model_validate(document)

    async def record_application(
        self,
        *,
        user_id: str,
        run_id: str,
        receipt: PatchApplicationReceipt,
    ) -> PatchProposal:
        try:
            document = await self._db()[
                self.collection_name
            ].find_one_and_update(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "patch_id": receipt.patch_id,
                    "revision": receipt.patch_revision,
                    "patch.patch_hash": receipt.patch_hash,
                    "status": PatchStatus.APPROVED.value,
                    "application": None,
                },
                {
                    "$set": {
                        "status": PatchStatus.APPLIED.value,
                        "application": receipt.model_dump(mode="python"),
                        "updated_at": utc_now(),
                    },
                    "$inc": {"version": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as error:
            raise PatchRepositoryError(
                "patch application could not be recorded"
            ) from error
        if document is None:
            raise PatchConflictError(
                "this patch is not approved and unapplied"
            )
        return PatchProposal.model_validate(document)

    async def mark_status(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
        revision: int,
        status: PatchStatus,
    ) -> PatchProposal:
        try:
            document = await self._db()[
                self.collection_name
            ].find_one_and_update(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "patch_id": patch_id,
                    "revision": revision,
                    "status": {"$in": list(_OPEN_STATUSES)},
                },
                {
                    "$set": {"status": status.value, "updated_at": utc_now()},
                    "$inc": {"version": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as error:
            raise PatchRepositoryError("patch status could not be set") from error
        if document is None:
            raise PatchConflictError("the patch is no longer open")
        return PatchProposal.model_validate(document)


class InMemoryPatchProposalRepository:
    """Process-local store with the same conditional semantics."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str, int], PatchProposal] = {}
        self._order: list[tuple[str, str, str, int]] = []

    def _key(self, proposal: PatchProposal) -> tuple[str, str, str, int]:
        return (
            proposal.user_id,
            proposal.run_id,
            proposal.patch_id,
            proposal.revision,
        )

    async def create(self, proposal: PatchProposal) -> PatchProposal:
        key = self._key(proposal)
        existing = self._entries.get(key)
        if existing is not None:
            if existing.patch.patch_hash == proposal.patch.patch_hash:
                return existing
            raise PatchConflictError(
                "a different patch already occupies this revision"
            )
        for other_key, other in list(self._entries.items()):
            if (
                other.user_id == proposal.user_id
                and other.run_id == proposal.run_id
                and other.status.value in _OPEN_STATUSES
            ):
                self._entries[other_key] = other.model_copy(
                    update={
                        "status": PatchStatus.SUPERSEDED,
                        "updated_at": proposal.created_at,
                    }
                )
        self._entries[key] = proposal
        self._order.append(key)
        return proposal

    async def get(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
        revision: int | None = None,
    ) -> PatchProposal | None:
        candidates = [
            item
            for item in self._entries.values()
            if item.user_id == user_id
            and item.run_id == run_id
            and item.patch_id == patch_id
            and (revision is None or item.revision == revision)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.revision)

    async def get_current(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> PatchProposal | None:
        for key in reversed(self._order):
            item = self._entries.get(key)
            if item is not None and item.user_id == user_id and item.run_id == run_id:
                return item
        return None

    async def decide(
        self,
        *,
        user_id: str,
        run_id: str,
        command: PatchApprovalCommand,
        decided_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> PatchProposal:
        binding = command.binding
        key = (user_id, run_id, binding.patch_id, binding.patch_revision)
        current = self._entries.get(key)
        if current is None or current.binding != binding:
            raise PatchConflictError(
                "the patch changed since it was shown; approve the new one"
            )
        replay = current.approval.decision_id == command.decision_id
        if current.status is not PatchStatus.AWAITING_APPROVAL and not replay:
            raise PatchConflictError("this patch has already been decided")
        moment = _as_utc(decided_at or utc_now())
        decided = current.model_copy(
            update={
                "status": _decided_status(command),
                "approval": _approval_for(
                    command,
                    decided_at=moment,
                    expires_at=expires_at,
                ),
                "updated_at": moment,
                "version": current.version + 1,
            }
        )
        self._entries[key] = decided
        return decided

    async def record_application(
        self,
        *,
        user_id: str,
        run_id: str,
        receipt: PatchApplicationReceipt,
    ) -> PatchProposal:
        key = (user_id, run_id, receipt.patch_id, receipt.patch_revision)
        current = self._entries.get(key)
        if (
            current is None
            or current.status is not PatchStatus.APPROVED
            or current.application is not None
            or current.patch.patch_hash != receipt.patch_hash
        ):
            raise PatchConflictError("this patch is not approved and unapplied")
        applied = current.model_copy(
            update={
                "status": PatchStatus.APPLIED,
                "application": receipt,
                "updated_at": utc_now(),
                "version": current.version + 1,
            }
        )
        self._entries[key] = applied
        return applied

    async def mark_status(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
        revision: int,
        status: PatchStatus,
    ) -> PatchProposal:
        key = (user_id, run_id, patch_id, revision)
        current = self._entries.get(key)
        if current is None or current.status.value not in _OPEN_STATUSES:
            raise PatchConflictError("the patch is no longer open")
        updated = current.model_copy(
            update={
                "status": status,
                "updated_at": utc_now(),
                "version": current.version + 1,
            }
        )
        self._entries[key] = updated
        return updated


__all__ = [
    "InMemoryPatchProposalRepository",
    "MongoPatchProposalRepository",
    "PatchConflictError",
    "PatchNotFoundError",
    "PatchProposalRepository",
    "PatchRepositoryError",
]
