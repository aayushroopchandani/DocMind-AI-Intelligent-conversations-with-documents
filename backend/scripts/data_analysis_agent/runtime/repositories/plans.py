from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from db.mongodb import get_db

from ..models.events import AnalysisEventType, AnalysisRunEvent
from ..models.plans import (
    AnalysisPlan,
    AnalysisPlanStatus,
    FinalPatchProposal,
    PlanApprovalRecord,
    PlanApprovalStatus,
    PlanRejectionReason,
)
from ..models.runs import (
    AnalysisRun,
    AnalysisRunOutcome,
    AnalysisRunPhase,
    AnalysisRunStatus,
    RunApprovalStatus,
)


class AnalysisPlanRepositoryError(RuntimeError):
    """Analysis-plan persistence failed."""


class AnalysisPlanNotFoundError(AnalysisPlanRepositoryError):
    """The tenant-scoped plan or patch does not exist."""


class AnalysisPlanConflictError(AnalysisPlanRepositoryError):
    """A stale revision, hash, or approval decision was supplied."""


@dataclass(frozen=True, slots=True)
class PlanDecisionResult:
    """One atomic plan/run/event approval boundary."""

    plan: AnalysisPlan
    run: AnalysisRun
    event: AnalysisRunEvent
    changed: bool


class AnalysisPlanRepository(Protocol):
    async def create_plan(self, plan: AnalysisPlan) -> AnalysisPlan: ...

    async def get_plan(
        self,
        *,
        user_id: str,
        run_id: str,
        plan_id: str,
    ) -> AnalysisPlan | None: ...

    async def get_current_plan(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> AnalysisPlan | None: ...

    async def list_reserved_write_targets(
        self,
        *,
        user_id: str,
        workspace_id: str,
        exclude_run_id: str,
        limit: int = 500,
    ) -> frozenset[str]: ...

    async def decide_plan(
        self,
        *,
        user_id: str,
        run_id: str,
        plan_id: str,
        expected_revision: int,
        expected_plan_hash: str,
        expected_input_signature: str,
        expected_run_version: int,
        status: PlanApprovalStatus,
        actor_user_id: str,
        decision_id: str,
        comment: str | None,
        decided_at: datetime,
        rejection_reason: PlanRejectionReason | None = None,
        trace_id: str | None = None,
    ) -> PlanDecisionResult: ...

    async def create_patch_proposal(
        self,
        proposal: FinalPatchProposal,
    ) -> FinalPatchProposal: ...

    async def get_patch_proposal(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
    ) -> FinalPatchProposal | None: ...

    async def decide_patch(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
        expected_patch_hash: str,
        expected_plan_hash: str,
        status: PlanApprovalStatus,
        actor_user_id: str,
        decision_id: str,
        comment: str | None,
        requested_at: datetime,
        decided_at: datetime,
    ) -> FinalPatchProposal: ...


class MongoAnalysisPlanRepository:
    plans_collection_name = "analysis_plans"
    patches_collection_name = "analysis_patch_proposals"
    runs_collection_name = "analysis_runs"
    events_collection_name = "analysis_run_events"

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
        except AnalysisPlanRepositoryError:
            raise
        except PyMongoError as exc:
            raise AnalysisPlanRepositoryError(
                "plan approval transaction failed; MongoDB transaction support "
                "is required"
            ) from exc

    async def create_plan(self, plan: AnalysisPlan) -> AnalysisPlan:
        database = self._db()

        async def create_transaction(session: Any) -> AnalysisPlan:
            current_document = await database[
                self.plans_collection_name
            ].find_one(
                {"user_id": plan.user_id, "run_id": plan.run_id},
                sort=[("revision", -1)],
                session=session,
            )
            if current_document is not None:
                current = _plan(current_document)
                if (
                    current.revision == plan.revision
                    and current.plan_hash == plan.plan_hash
                ):
                    return current
                if current.revision >= plan.revision:
                    raise AnalysisPlanConflictError(
                        "plan revision must increase monotonically"
                    )
                await database[self.plans_collection_name].update_many(
                    {
                        "user_id": plan.user_id,
                        "run_id": plan.run_id,
                        "revision": {"$lt": plan.revision},
                        "status": {
                            "$ne": AnalysisPlanStatus.SUPERSEDED.value,
                        },
                    },
                    {
                        "$set": {
                            "status": AnalysisPlanStatus.SUPERSEDED.value,
                            "reservation_active": False,
                            "updated_at": plan.created_at,
                        }
                    },
                    session=session,
                )
            await database[self.plans_collection_name].insert_one(
                plan.model_dump(mode="python"),
                session=session,
            )
            return plan

        try:
            return await self._in_transaction(create_transaction)
        except AnalysisPlanRepositoryError as exc:
            if not isinstance(exc.__cause__, DuplicateKeyError):
                raise
        except DuplicateKeyError:
            pass
        try:
            existing = await self._find_plan_revision(
                user_id=plan.user_id,
                run_id=plan.run_id,
                revision=plan.revision,
            )
            if existing is not None and existing.plan_hash == plan.plan_hash:
                return existing
            raise AnalysisPlanConflictError(
                "plan revision or workbook target is already reserved"
            )
        except PyMongoError as exc:
            raise AnalysisPlanRepositoryError("analysis plan could not be persisted") from exc

    async def get_plan(
        self,
        *,
        user_id: str,
        run_id: str,
        plan_id: str,
    ) -> AnalysisPlan | None:
        try:
            document = await self._db()[self.plans_collection_name].find_one(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "plan_id": plan_id,
                }
            )
        except PyMongoError as exc:
            raise AnalysisPlanRepositoryError("analysis plan could not be read") from exc
        return _plan(document) if document is not None else None

    async def get_current_plan(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> AnalysisPlan | None:
        try:
            cursor = (
                self._db()[self.plans_collection_name]
                .find({"user_id": user_id, "run_id": run_id})
                .sort([("revision", -1)])
                .limit(1)
            )
            documents = await cursor.to_list(length=1)
        except PyMongoError as exc:
            raise AnalysisPlanRepositoryError("analysis plan could not be read") from exc
        return _plan(documents[0]) if documents else None

    async def list_reserved_write_targets(
        self,
        *,
        user_id: str,
        workspace_id: str,
        exclude_run_id: str,
        limit: int = 500,
    ) -> frozenset[str]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        database = self._db()
        try:
            documents = await database[self.plans_collection_name].find(
                {
                    "user_id": user_id,
                    "workspace_id": workspace_id,
                    "run_id": {"$ne": exclude_run_id},
                    "status": {
                        "$in": [
                            AnalysisPlanStatus.READY.value,
                            AnalysisPlanStatus.AWAITING_PLAN_APPROVAL.value,
                            AnalysisPlanStatus.APPROVED.value,
                        ]
                    },
                    "reservation_active": True,
                    "write_target_keys.0": {"$exists": True},
                },
                {
                    "run_id": 1,
                    "plan_id": 1,
                    "plan_hash": 1,
                    "write_target_keys": 1,
                },
            ).to_list(length=limit)
            run_ids = tuple(
                dict.fromkeys(str(document["run_id"]) for document in documents)
            )
            run_documents = (
                await database[self.runs_collection_name].find(
                    {
                        "user_id": user_id,
                        "workspace_id": workspace_id,
                        "run_id": {"$in": list(run_ids)},
                    },
                    {
                        "run_id": 1,
                        "status": 1,
                        "outcome": 1,
                        "cancellation_requested": 1,
                        "pause_requested": 1,
                        "current_plan_id": 1,
                        "current_plan_hash": 1,
                    },
                ).to_list(length=len(run_ids))
                if run_ids
                else []
            )
        except PyMongoError as exc:
            raise AnalysisPlanRepositoryError(
                "write-target reservations could not be read"
            ) from exc
        runs = {
            str(document["run_id"]): document for document in run_documents
        }
        active_documents: list[Mapping[str, Any]] = []
        stale_plan_ids: list[str] = []
        for document in documents:
            if _reservation_is_live(
                document,
                runs.get(str(document["run_id"])),
            ):
                active_documents.append(document)
            else:
                stale_plan_ids.append(str(document["plan_id"]))
        if stale_plan_ids:
            try:
                await database[self.plans_collection_name].update_many(
                    {
                        "user_id": user_id,
                        "plan_id": {"$in": stale_plan_ids},
                        "reservation_active": True,
                    },
                    {
                        "$set": {
                            "status": AnalysisPlanStatus.SUPERSEDED.value,
                            "reservation_active": False,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    },
                )
            except PyMongoError as exc:
                raise AnalysisPlanRepositoryError(
                    "stale write reservations could not be released"
                ) from exc
        return frozenset(
            str(target)
            for document in active_documents
            for target in document.get("write_target_keys", ())
        )

    async def decide_plan(
        self,
        *,
        user_id: str,
        run_id: str,
        plan_id: str,
        expected_revision: int,
        expected_plan_hash: str,
        expected_input_signature: str,
        expected_run_version: int,
        status: PlanApprovalStatus,
        actor_user_id: str,
        decision_id: str,
        comment: str | None,
        decided_at: datetime,
        rejection_reason: PlanRejectionReason | None = None,
        trace_id: str | None = None,
    ) -> PlanDecisionResult:
        if status not in {
            PlanApprovalStatus.APPROVED,
            PlanApprovalStatus.REJECTED,
        }:
            raise ValueError("plan decision must approve or reject")
        if expected_run_version < 1:
            raise ValueError("expected_run_version must be at least one")
        requested_decision_time = _utc(decided_at)
        target_status = (
            AnalysisPlanStatus.APPROVED
            if status == PlanApprovalStatus.APPROVED
            else AnalysisPlanStatus.REJECTED
        )
        target_run_approval = RunApprovalStatus(status.value)
        target_outcome = (
            AnalysisRunOutcome.PLAN_READY
            if status == PlanApprovalStatus.APPROVED
            else AnalysisRunOutcome.REJECTED
        )
        event_type = (
            AnalysisEventType.PLAN_APPROVED
            if status == PlanApprovalStatus.APPROVED
            else AnalysisEventType.PLAN_REJECTED
        )
        deduplication_key = f"plan-decision:{decision_id}"
        payload = {
            "plan_id": plan_id,
            "revision": expected_revision,
            "plan_hash": expected_plan_hash,
            "decision_id": decision_id,
            "rejection_reason": (
                rejection_reason.value if rejection_reason is not None else None
            ),
        }
        database = self._db()

        async def decision_transaction(session: Any) -> PlanDecisionResult:
            duplicate_document = await database[
                self.events_collection_name
            ].find_one(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "deduplication_key": deduplication_key,
                },
                session=session,
            )
            if duplicate_document is not None:
                event = _event(duplicate_document)
                plan_document = await database[
                    self.plans_collection_name
                ].find_one(
                    {"user_id": user_id, "run_id": run_id, "plan_id": plan_id},
                    session=session,
                )
                run_document = await database[self.runs_collection_name].find_one(
                    {"user_id": user_id, "run_id": run_id},
                    session=session,
                )
                if plan_document is None or run_document is None:
                    raise AnalysisPlanRepositoryError(
                        "approval event exists without its plan or run"
                    )
                plan = _plan(plan_document)
                run = _run(run_document)
                if not _is_same_plan_decision(
                    plan=plan,
                    run=run,
                    event=event,
                    revision=expected_revision,
                    plan_hash=expected_plan_hash,
                    input_signature=expected_input_signature,
                    status=status,
                    outcome=target_outcome,
                    event_type=event_type,
                    decision_id=decision_id,
                    payload=payload,
                ):
                    raise AnalysisPlanConflictError(
                        "decision ID was reused for a different approval"
                    )
                return PlanDecisionResult(
                    plan=plan,
                    run=run,
                    event=event,
                    changed=False,
                )

            plan_document = await database[self.plans_collection_name].find_one(
                {"user_id": user_id, "run_id": run_id, "plan_id": plan_id},
                session=session,
            )
            if plan_document is None:
                raise AnalysisPlanNotFoundError("analysis plan not found")
            run_document = await database[self.runs_collection_name].find_one(
                {"user_id": user_id, "run_id": run_id},
                session=session,
            )
            if run_document is None:
                raise AnalysisPlanNotFoundError("analysis run not found")
            plan = _plan(plan_document)
            run = _run(run_document)
            if (
                plan.revision != expected_revision
                or plan.plan_hash != expected_plan_hash
                or plan.input_signature != expected_input_signature
            ):
                raise AnalysisPlanConflictError("plan approval is stale")
            if (
                plan.status != AnalysisPlanStatus.AWAITING_PLAN_APPROVAL
                or plan.approval.status != PlanApprovalStatus.PENDING
            ):
                raise AnalysisPlanConflictError(
                    "plan approval conflicts with an existing decision"
                )
            if run.version != expected_run_version:
                raise AnalysisPlanConflictError("analysis run changed before approval")
            if (
                run.status != AnalysisRunStatus.WAITING
                or run.phase != AnalysisRunPhase.APPROVAL
                or run.outcome != AnalysisRunOutcome.PLAN_READY
                or run.current_plan_id != plan_id
                or run.current_plan_revision != expected_revision
                or run.current_plan_hash != expected_plan_hash
                or run.plan_approval_status != RunApprovalStatus.PENDING
                or run.cancellation_requested
                or run.pause_requested
                or run.worker_id is not None
            ):
                raise AnalysisPlanConflictError(
                    "analysis run is no longer awaiting this plan decision"
                )

            operation_time = max(
                requested_decision_time,
                plan.created_at,
                plan.updated_at,
                run.created_at,
                run.updated_at,
                *(value for value in (run.started_at,) if value is not None),
            )
            approval = PlanApprovalRecord(
                status=status,
                actor_user_id=actor_user_id,
                comment=comment,
                rejection_reason=rejection_reason,
                requested_at=plan.approval.requested_at,
                decided_at=operation_time,
                decision_id=decision_id,
            )
            decided_plan = AnalysisPlan.model_validate(
                plan.model_copy(
                    update={
                        "status": target_status,
                        "approval": approval,
                        "reservation_active": (
                            plan.reservation_active
                            if status == PlanApprovalStatus.APPROVED
                            else False
                        ),
                        "updated_at": operation_time,
                    }
                ).model_dump(mode="python")
            )
            decided_run = AnalysisRun.model_validate(
                run.model_copy(
                    update={
                        "status": AnalysisRunStatus.SUCCEEDED,
                        "phase": AnalysisRunPhase.COMPLETED,
                        "outcome": target_outcome,
                        "plan_approval_status": target_run_approval,
                        "updated_at": operation_time,
                        "completed_at": operation_time,
                        "version": run.version + 1,
                        "last_event_sequence": run.last_event_sequence + 1,
                    }
                ).model_dump(mode="python")
            )

            stored_plan = await database[
                self.plans_collection_name
            ].find_one_and_update(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "plan_id": plan_id,
                    "revision": expected_revision,
                    "plan_hash": expected_plan_hash,
                    "input_signature": expected_input_signature,
                    "status": AnalysisPlanStatus.AWAITING_PLAN_APPROVAL.value,
                    "approval.status": PlanApprovalStatus.PENDING.value,
                },
                {"$set": decided_plan.model_dump(mode="python")},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if stored_plan is None:
                raise AnalysisPlanConflictError("plan changed during approval")
            stored_run = await database[self.runs_collection_name].find_one_and_update(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "version": expected_run_version,
                    "status": AnalysisRunStatus.WAITING.value,
                    "phase": AnalysisRunPhase.APPROVAL.value,
                    "outcome": AnalysisRunOutcome.PLAN_READY.value,
                    "current_plan_id": plan_id,
                    "current_plan_revision": expected_revision,
                    "current_plan_hash": expected_plan_hash,
                    "plan_approval_status": RunApprovalStatus.PENDING.value,
                    "cancellation_requested": False,
                    "pause_requested": {"$ne": True},
                    "worker_id": None,
                },
                {"$set": decided_run.model_dump(mode="python")},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if stored_run is None:
                raise AnalysisPlanConflictError("run changed during approval")
            event = AnalysisRunEvent(
                run_id=run_id,
                user_id=user_id,
                workspace_id=run.workspace_id,
                sequence=decided_run.last_event_sequence,
                event_type=event_type,
                status=decided_run.status,
                phase=decided_run.phase,
                payload=payload,
                deduplication_key=deduplication_key,
                trace_id=trace_id,
                occurred_at=operation_time,
            )
            await database[self.events_collection_name].insert_one(
                event.model_dump(mode="python"),
                session=session,
            )
            return PlanDecisionResult(
                plan=decided_plan,
                run=decided_run,
                event=event,
                changed=True,
            )

        return await self._in_transaction(decision_transaction)

    async def create_patch_proposal(
        self,
        proposal: FinalPatchProposal,
    ) -> FinalPatchProposal:
        if proposal.approval.status != PlanApprovalStatus.PENDING:
            raise ValueError("new patch proposals must await approval")
        try:
            await self._db()[self.patches_collection_name].insert_one(
                proposal.model_dump(mode="python")
            )
            return proposal
        except DuplicateKeyError:
            existing = await self.get_patch_proposal(
                user_id=proposal.user_id,
                run_id=proposal.run_id,
                patch_id=proposal.patch_id,
            )
            if (
                existing is not None
                and existing.patch_hash == proposal.patch_hash
                and existing.plan_hash == proposal.plan_hash
            ):
                return existing
            raise AnalysisPlanConflictError(
                "patch identity is already occupied by different content"
            )
        except PyMongoError as exc:
            raise AnalysisPlanRepositoryError(
                "patch proposal could not be persisted"
            ) from exc

    async def get_patch_proposal(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
    ) -> FinalPatchProposal | None:
        try:
            document = await self._db()[self.patches_collection_name].find_one(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "patch_id": patch_id,
                }
            )
        except PyMongoError as exc:
            raise AnalysisPlanRepositoryError("patch proposal could not be read") from exc
        return _patch(document) if document is not None else None

    async def decide_patch(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
        expected_patch_hash: str,
        expected_plan_hash: str,
        status: PlanApprovalStatus,
        actor_user_id: str,
        decision_id: str,
        comment: str | None,
        requested_at: datetime,
        decided_at: datetime,
    ) -> FinalPatchProposal:
        if status not in {
            PlanApprovalStatus.APPROVED,
            PlanApprovalStatus.REJECTED,
        }:
            raise ValueError("patch decision must approve or reject")
        requested_at = _utc(requested_at)
        decided_at = max(_utc(decided_at), requested_at)
        approval = PlanApprovalRecord(
            status=status,
            actor_user_id=actor_user_id,
            comment=comment,
            requested_at=requested_at,
            decided_at=decided_at,
            decision_id=decision_id,
        )
        query = {
            "user_id": user_id,
            "run_id": run_id,
            "patch_id": patch_id,
            "patch_hash": expected_patch_hash,
            "plan_hash": expected_plan_hash,
            "approval.status": PlanApprovalStatus.PENDING.value,
        }
        try:
            document = await self._db()[self.patches_collection_name].find_one_and_update(
                query,
                {
                    "$set": {
                        "approval": approval.model_dump(mode="python"),
                        "updated_at": decided_at,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise AnalysisPlanRepositoryError(
                "patch approval could not be recorded"
            ) from exc
        if document is not None:
            return _patch(document)
        existing = await self.get_patch_proposal(
            user_id=user_id,
            run_id=run_id,
            patch_id=patch_id,
        )
        if existing is None:
            raise AnalysisPlanNotFoundError("patch proposal not found")
        if (
            existing.patch_hash == expected_patch_hash
            and existing.plan_hash == expected_plan_hash
            and existing.approval.status == status
            and existing.approval.decision_id == decision_id
        ):
            return existing
        raise AnalysisPlanConflictError(
            "patch approval is stale or conflicts with an existing decision"
        )

    async def _find_plan_revision(
        self,
        *,
        user_id: str,
        run_id: str,
        revision: int,
    ) -> AnalysisPlan | None:
        try:
            document = await self._db()[self.plans_collection_name].find_one(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "revision": revision,
                }
            )
        except PyMongoError as exc:
            raise AnalysisPlanRepositoryError("analysis plan could not be read") from exc
        return _plan(document) if document is not None else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _without_id(document: Mapping[str, Any]) -> dict[str, Any]:
    output = dict(document)
    output.pop("_id", None)
    return _normalize_datetimes(output)


def _normalize_datetimes(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, dict):
        return {key: _normalize_datetimes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_datetimes(item) for item in value]
    return value


def _plan(document: Mapping[str, Any]) -> AnalysisPlan:
    return AnalysisPlan.model_validate(_without_id(document))


def _patch(document: Mapping[str, Any]) -> FinalPatchProposal:
    return FinalPatchProposal.model_validate(_without_id(document))


def _run(document: Mapping[str, Any]) -> AnalysisRun:
    return AnalysisRun.model_validate(_without_id(document))


def _event(document: Mapping[str, Any]) -> AnalysisRunEvent:
    return AnalysisRunEvent.model_validate(_without_id(document))


def _is_same_plan_decision(
    *,
    plan: AnalysisPlan,
    run: AnalysisRun,
    event: AnalysisRunEvent,
    revision: int,
    plan_hash: str,
    input_signature: str,
    status: PlanApprovalStatus,
    outcome: AnalysisRunOutcome,
    event_type: AnalysisEventType,
    decision_id: str,
    payload: Mapping[str, Any],
) -> bool:
    return (
        plan.revision == revision
        and plan.plan_hash == plan_hash
        and plan.input_signature == input_signature
        and plan.approval.status == status
        and plan.approval.decision_id == decision_id
        and run.status == AnalysisRunStatus.SUCCEEDED
        and run.outcome == outcome
        and run.current_plan_id == plan.plan_id
        and run.current_plan_hash == plan_hash
        and run.plan_approval_status == RunApprovalStatus(status.value)
        and event.event_type == event_type
        and event.payload == dict(payload)
    )


def _reservation_is_live(
    plan_document: Mapping[str, Any],
    run_document: Mapping[str, Any] | None,
) -> bool:
    if (
        run_document is None
        or run_document.get("cancellation_requested") is True
        or run_document.get("pause_requested") is True
    ):
        return False
    status = run_document.get("status")
    if status == AnalysisRunStatus.ACTIVE.value:
        return True
    if status not in {
        AnalysisRunStatus.WAITING.value,
        AnalysisRunStatus.SUCCEEDED.value,
    }:
        return False
    return (
        run_document.get("outcome") == AnalysisRunOutcome.PLAN_READY.value
        and run_document.get("current_plan_id") == plan_document.get("plan_id")
        and run_document.get("current_plan_hash")
        == plan_document.get("plan_hash")
    )


__all__ = [
    "AnalysisPlanConflictError",
    "AnalysisPlanNotFoundError",
    "AnalysisPlanRepository",
    "AnalysisPlanRepositoryError",
    "MongoAnalysisPlanRepository",
    "PlanDecisionResult",
]
