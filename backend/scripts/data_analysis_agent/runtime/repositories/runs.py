from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, TypeVar

from pydantic import JsonValue
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from db.mongodb import get_db
from scripts.data_analysis_agent.runtime.models.events import (
    AnalysisEventType,
    AnalysisRunEvent,
)
from scripts.data_analysis_agent.runtime.models.runs import (
    AnalysisRun,
    AnalysisRunStatus,
)


class AnalysisRunStoreError(RuntimeError):
    """Base error for durable analysis-run persistence."""


class AnalysisRunNotFoundError(AnalysisRunStoreError):
    """The requested run is absent or outside the authenticated tenant."""


class AnalysisRunConflictError(AnalysisRunStoreError):
    """A compare-and-swap mutation lost a race."""


class AnalysisRunIdempotencyConflictError(AnalysisRunConflictError):
    """An idempotency key was reused for a different operation."""


class AnalysisRunLeaseConflictError(AnalysisRunConflictError):
    """An execution lease is unavailable or no longer owned by the caller."""


@dataclass(frozen=True, slots=True)
class CreateRunResult:
    run: AnalysisRun
    event: AnalysisRunEvent
    created: bool


@dataclass(frozen=True, slots=True)
class RunMutationResult:
    run: AnalysisRun
    event: AnalysisRunEvent | None
    changed: bool


class AnalysisRunStore(Protocol):
    async def create_run(
        self,
        *,
        run: AnalysisRun,
        event_type: AnalysisEventType,
        payload: Mapping[str, JsonValue] | None = None,
        trace_id: str | None = None,
    ) -> CreateRunResult: ...

    async def get_run(self, *, user_id: str, run_id: str) -> AnalysisRun | None: ...

    async def get_run_by_idempotency_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> AnalysisRun | None: ...

    async def list_runs(
        self,
        *,
        user_id: str,
        workspace_id: str | None = None,
        status: AnalysisRunStatus | None = None,
        before_created_at: datetime | None = None,
        before_run_id: str | None = None,
        limit: int = 50,
    ) -> tuple[AnalysisRun, ...]: ...

    async def list_events(
        self,
        *,
        user_id: str,
        run_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[AnalysisRunEvent, ...]: ...

    async def get_event_by_deduplication_key(
        self,
        *,
        user_id: str,
        run_id: str,
        deduplication_key: str,
    ) -> AnalysisRunEvent | None: ...

    async def mutate_with_event(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int,
        updates: Mapping[str, Any],
        event_type: AnalysisEventType,
        payload: Mapping[str, JsonValue] | None = None,
        deduplication_key: str | None = None,
        trace_id: str | None = None,
        additional_filter: Mapping[str, Any] | None = None,
    ) -> RunMutationResult: ...

    async def renew_execution_lease(
        self,
        *,
        user_id: str,
        run_id: str,
        worker_id: str,
        lease_attempt: int,
        current_time: datetime,
        lease_expires_at: datetime,
    ) -> AnalysisRun: ...

    async def release_execution_lease(
        self,
        *,
        user_id: str,
        run_id: str,
        worker_id: str,
        lease_attempt: int,
    ) -> AnalysisRun: ...

    async def list_recoverable_runs(
        self,
        *,
        current_time: datetime,
        limit: int = 100,
    ) -> tuple[AnalysisRun, ...]: ...

    async def list_abandoned_cancellations(
        self,
        *,
        current_time: datetime,
        limit: int = 100,
    ) -> tuple[AnalysisRun, ...]: ...

    async def list_expirable_runs(
        self,
        *,
        current_time: datetime,
        limit: int = 100,
    ) -> tuple[AnalysisRun, ...]: ...


_T = TypeVar("_T")
_TransactionCallback = Callable[[Any], Awaitable[_T]]
_IMMUTABLE_MUTATION_FIELDS = frozenset(
    {
        "_id",
        "schema_version",
        "run_id",
        "user_id",
        "workspace_id",
        "chat_id",
        "active_artifact_id",
        "inputs_ready",
        "input_artifact_version_ids",
        "input_dataset_versions",
        "idempotency_key",
        "request_fingerprint",
        "created_at",
        "version",
        "last_event_sequence",
    }
)
_INPUT_INITIALIZATION_FIELDS = frozenset(
    {
        "active_artifact_id",
        "inputs_ready",
        "input_artifact_version_ids",
        "input_dataset_versions",
    }
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_datetimes(value: Any) -> Any:
    """Motor returns naive UTC by default; make domain validation deterministic."""

    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, dict):
        return {key: _normalize_datetimes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_datetimes(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_datetimes(item) for item in value)
    return value


def _without_mongo_id(document: Mapping[str, Any]) -> dict[str, Any]:
    output = dict(document)
    output.pop("_id", None)
    return _normalize_datetimes(output)


def _run_from_document(document: Mapping[str, Any]) -> AnalysisRun:
    return AnalysisRun.model_validate(_without_mongo_id(document))


def _event_from_document(document: Mapping[str, Any]) -> AnalysisRunEvent:
    return AnalysisRunEvent.model_validate(_without_mongo_id(document))


def _mongo_document(model: AnalysisRun | AnalysisRunEvent) -> dict[str, Any]:
    return model.model_dump(mode="python")


def _validate_pagination(*, after_sequence: int, limit: int) -> None:
    if after_sequence < 0:
        raise ValueError("after_sequence must be non-negative")
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")


def _creation_deduplication_key(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"create:{digest}"


class MongoAnalysisRunStore:
    """
    Mongo-backed run/event store.

    Lifecycle mutations and their visible events share one Mongo transaction.
    The configured Mongo deployment therefore needs transaction support
    (Atlas or another replica set). Lease heartbeats are operational metadata:
    they intentionally do not consume lifecycle versions or SSE sequences.
    """

    runs_collection_name = "analysis_runs"
    events_collection_name = "analysis_run_events"

    def __init__(self, database: Any | None = None) -> None:
        self._database = database

    def _db(self) -> Any:
        return self._database if self._database is not None else get_db()

    async def _in_transaction(self, callback: _TransactionCallback[_T]) -> _T:
        database = self._db()
        try:
            session_context = await database.client.start_session()
            async with session_context as session:
                return await session.with_transaction(callback)
        except AnalysisRunStoreError:
            raise
        except PyMongoError as exc:
            raise AnalysisRunStoreError(
                "analysis run transaction failed; MongoDB transaction support "
                "is required"
            ) from exc

    async def create_run(
        self,
        *,
        run: AnalysisRun,
        event_type: AnalysisEventType = AnalysisEventType.RUN_CREATED,
        payload: Mapping[str, JsonValue] | None = None,
        trace_id: str | None = None,
    ) -> CreateRunResult:
        if run.version != 0 or run.last_event_sequence != 0:
            raise ValueError("new runs must start at version and sequence zero")
        if run.status != AnalysisRunStatus.CREATED:
            raise ValueError("new runs must have created status")

        database = self._db()
        stored_run = run.model_copy(
            update={
                "version": 1,
                "last_event_sequence": 1,
            }
        )
        event = AnalysisRunEvent(
            run_id=stored_run.run_id,
            user_id=stored_run.user_id,
            workspace_id=stored_run.workspace_id,
            sequence=1,
            event_type=event_type,
            status=stored_run.status,
            phase=stored_run.phase,
            payload=dict(payload or {}),
            # Request idempotency keys are intentionally opaque and may not
            # satisfy the public event identifier grammar. Hashing also keeps
            # them out of the event stream.
            deduplication_key=_creation_deduplication_key(
                stored_run.idempotency_key
            ),
            trace_id=trace_id,
            occurred_at=stored_run.created_at,
        )

        async def create_transaction(session: Any) -> CreateRunResult:
            await database[self.runs_collection_name].insert_one(
                _mongo_document(stored_run),
                session=session,
            )
            await database[self.events_collection_name].insert_one(
                _mongo_document(event),
                session=session,
            )
            return CreateRunResult(run=stored_run, event=event, created=True)

        try:
            return await self._in_transaction(create_transaction)
        except AnalysisRunStoreError as exc:
            if not isinstance(exc.__cause__, DuplicateKeyError):
                raise
        except DuplicateKeyError:
            # Test doubles and alternate async Mongo clients may surface the
            # duplicate directly rather than under the store wrapper.
            pass

        existing_document = await database[self.runs_collection_name].find_one(
            {
                "user_id": run.user_id,
                "idempotency_key": run.idempotency_key,
            }
        )
        if existing_document is None:
            raise AnalysisRunConflictError("run identity is already in use")

        existing = _run_from_document(existing_document)
        if existing.request_fingerprint != run.request_fingerprint:
            raise AnalysisRunIdempotencyConflictError(
                "idempotency key was already used for a different request"
            )

        existing_event_document = await database[
            self.events_collection_name
        ].find_one(
            {
                "user_id": existing.user_id,
                "run_id": existing.run_id,
                "sequence": 1,
            }
        )
        if existing_event_document is None:
            raise AnalysisRunStoreError(
                "idempotent run exists without its creation event"
            )
        return CreateRunResult(
            run=existing,
            event=_event_from_document(existing_event_document),
            created=False,
        )

    async def get_run(self, *, user_id: str, run_id: str) -> AnalysisRun | None:
        try:
            document = await self._db()[self.runs_collection_name].find_one(
                {"user_id": user_id, "run_id": run_id}
            )
        except PyMongoError as exc:
            raise AnalysisRunStoreError("analysis run could not be read") from exc
        return _run_from_document(document) if document is not None else None

    async def get_run_by_idempotency_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> AnalysisRun | None:
        try:
            document = await self._db()[self.runs_collection_name].find_one(
                {
                    "user_id": user_id,
                    "idempotency_key": idempotency_key,
                }
            )
        except PyMongoError as exc:
            raise AnalysisRunStoreError("analysis run could not be read") from exc
        return _run_from_document(document) if document is not None else None

    async def list_runs(
        self,
        *,
        user_id: str,
        workspace_id: str | None = None,
        status: AnalysisRunStatus | None = None,
        before_created_at: datetime | None = None,
        before_run_id: str | None = None,
        limit: int = 50,
    ) -> tuple[AnalysisRun, ...]:
        if not 1 <= limit <= 101:
            raise ValueError("limit must be between 1 and 101")
        if (before_created_at is None) != (before_run_id is None):
            raise ValueError(
                "before_created_at and before_run_id must be supplied together"
            )
        query: dict[str, Any] = {"user_id": user_id}
        if workspace_id is not None:
            query["workspace_id"] = workspace_id
        if status is not None:
            query["status"] = status.value
        if before_created_at is not None:
            cursor_time = _as_utc(before_created_at)
            query["$or"] = [
                {"created_at": {"$lt": cursor_time}},
                {
                    "created_at": cursor_time,
                    "run_id": {"$lt": before_run_id},
                },
            ]
        try:
            cursor = (
                self._db()[self.runs_collection_name]
                .find(query, {"_id": 0})
                .sort([("created_at", -1), ("run_id", -1)])
                .limit(limit)
            )
            documents = await cursor.to_list(length=limit)
        except PyMongoError as exc:
            raise AnalysisRunStoreError("analysis runs could not be listed") from exc
        return tuple(_run_from_document(document) for document in documents)

    async def list_events(
        self,
        *,
        user_id: str,
        run_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[AnalysisRunEvent, ...]:
        _validate_pagination(after_sequence=after_sequence, limit=limit)
        try:
            cursor = (
                self._db()[self.events_collection_name]
                .find(
                    {
                        "user_id": user_id,
                        "run_id": run_id,
                        "sequence": {"$gt": after_sequence},
                    },
                    {"_id": 0},
                )
                .sort("sequence", 1)
                .limit(limit)
            )
            documents = await cursor.to_list(length=limit)
        except PyMongoError as exc:
            raise AnalysisRunStoreError("analysis events could not be read") from exc
        return tuple(_event_from_document(document) for document in documents)

    async def get_event_by_deduplication_key(
        self,
        *,
        user_id: str,
        run_id: str,
        deduplication_key: str,
    ) -> AnalysisRunEvent | None:
        try:
            document = await self._db()[self.events_collection_name].find_one(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "deduplication_key": deduplication_key,
                }
            )
        except PyMongoError as exc:
            raise AnalysisRunStoreError("analysis event could not be read") from exc
        return _event_from_document(document) if document is not None else None

    async def mutate_with_event(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int,
        updates: Mapping[str, Any],
        event_type: AnalysisEventType,
        payload: Mapping[str, JsonValue] | None = None,
        deduplication_key: str | None = None,
        trace_id: str | None = None,
        additional_filter: Mapping[str, Any] | None = None,
    ) -> RunMutationResult:
        if expected_version < 1:
            raise ValueError("expected_version must be at least one")
        forbidden = _IMMUTABLE_MUTATION_FIELDS.intersection(updates)
        initializing_inputs = (
            forbidden == _INPUT_INITIALIZATION_FIELDS
            and updates.get("inputs_ready") is True
            and additional_filter is not None
            and additional_filter.get("inputs_ready") is False
            and additional_filter.get("status")
            == AnalysisRunStatus.CREATED.value
        )
        if forbidden and not initializing_inputs:
            raise ValueError(
                "immutable run fields cannot be changed: "
                + ", ".join(sorted(forbidden))
            )

        database = self._db()

        async def mutation_transaction(session: Any) -> RunMutationResult:
            if deduplication_key:
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
                    duplicate = _event_from_document(duplicate_document)
                    if (
                        duplicate.event_type != event_type
                        or duplicate.payload != dict(payload or {})
                    ):
                        raise AnalysisRunIdempotencyConflictError(
                            "event deduplication key was reused for a different event"
                        )
                    current_document = await database[
                        self.runs_collection_name
                    ].find_one(
                        {"user_id": user_id, "run_id": run_id},
                        session=session,
                    )
                    if current_document is None:
                        raise AnalysisRunNotFoundError("analysis run not found")
                    return RunMutationResult(
                        run=_run_from_document(current_document),
                        event=duplicate,
                        changed=False,
                    )

            current_document = await database[self.runs_collection_name].find_one(
                {"user_id": user_id, "run_id": run_id},
                session=session,
            )
            if current_document is None:
                raise AnalysisRunNotFoundError("analysis run not found")
            current = _run_from_document(current_document)
            if current.version != expected_version:
                raise AnalysisRunConflictError(
                    f"stale run version: expected {expected_version}, "
                    f"found {current.version}"
                )

            next_sequence = current.last_event_sequence + 1
            next_version = current.version + 1
            normalized_updates = dict(updates)
            normalized_updates["version"] = next_version
            normalized_updates["last_event_sequence"] = next_sequence

            # Validate the complete domain object before touching Mongo. This
            # catches invalid terminal outcomes/leases inside the transaction.
            candidate = current.model_copy(update=normalized_updates)
            candidate = AnalysisRun.model_validate(candidate.model_dump(mode="python"))

            update_filter: dict[str, Any] = {
                "user_id": user_id,
                "run_id": run_id,
                "version": expected_version,
            }
            if additional_filter:
                update_filter = {
                    "$and": [
                        update_filter,
                        dict(additional_filter),
                    ]
                }
            updated_document = await database[
                self.runs_collection_name
            ].find_one_and_update(
                update_filter,
                {"$set": _mongo_document(candidate)},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated_document is None:
                raise AnalysisRunConflictError(
                    "run changed or a mutation precondition was not met"
                )
            updated = _run_from_document(updated_document)
            event = AnalysisRunEvent(
                run_id=updated.run_id,
                user_id=updated.user_id,
                workspace_id=updated.workspace_id,
                sequence=next_sequence,
                event_type=event_type,
                status=updated.status,
                phase=updated.phase,
                payload=dict(payload or {}),
                deduplication_key=deduplication_key,
                trace_id=trace_id,
                occurred_at=updated.updated_at,
            )
            await database[self.events_collection_name].insert_one(
                _mongo_document(event),
                session=session,
            )
            return RunMutationResult(run=updated, event=event, changed=True)

        try:
            return await self._in_transaction(mutation_transaction)
        except AnalysisRunStoreError as exc:
            if not (
                deduplication_key
                and isinstance(exc.__cause__, DuplicateKeyError)
            ):
                raise
        except DuplicateKeyError:
            if not deduplication_key:
                raise

        # A concurrent identical command may have committed after this
        # transaction's snapshot. Recover it as an idempotent replay.
        duplicate = await self.get_event_by_deduplication_key(
            user_id=user_id,
            run_id=run_id,
            deduplication_key=deduplication_key,
        )
        if (
            duplicate is None
            or duplicate.event_type != event_type
            or duplicate.payload != dict(payload or {})
        ):
            raise AnalysisRunIdempotencyConflictError(
                "event deduplication key was reused incompatibly"
            )
        current = await self.get_run(user_id=user_id, run_id=run_id)
        if current is None:
            raise AnalysisRunNotFoundError("analysis run not found")
        return RunMutationResult(run=current, event=duplicate, changed=False)

    async def renew_execution_lease(
        self,
        *,
        user_id: str,
        run_id: str,
        worker_id: str,
        lease_attempt: int,
        current_time: datetime,
        lease_expires_at: datetime,
    ) -> AnalysisRun:
        if lease_attempt < 1:
            raise ValueError("lease_attempt must be at least one")
        current_time = _as_utc(current_time)
        lease_expires_at = _as_utc(lease_expires_at)
        if lease_expires_at <= current_time:
            raise ValueError("lease expiry must be in the future")
        try:
            document = await self._db()[
                self.runs_collection_name
            ].find_one_and_update(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "status": AnalysisRunStatus.ACTIVE.value,
                    "worker_id": worker_id,
                    "lease_attempt": lease_attempt,
                    "lease_expires_at": {"$gt": current_time},
                    "cancellation_requested": False,
                    "$or": [
                        {"expires_at": None},
                        {"expires_at": {"$gt": current_time}},
                    ],
                },
                {"$set": {"lease_expires_at": lease_expires_at}},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise AnalysisRunStoreError("execution lease could not be renewed") from exc
        if document is None:
            raise AnalysisRunLeaseConflictError(
                "execution lease expired, was cancelled, or is owned elsewhere"
            )
        return _run_from_document(document)

    async def release_execution_lease(
        self,
        *,
        user_id: str,
        run_id: str,
        worker_id: str,
        lease_attempt: int,
    ) -> AnalysisRun:
        if lease_attempt < 1:
            raise ValueError("lease_attempt must be at least one")
        try:
            document = await self._db()[
                self.runs_collection_name
            ].find_one_and_update(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "worker_id": worker_id,
                    "lease_attempt": lease_attempt,
                },
                {
                    "$set": {
                        "worker_id": None,
                        "lease_expires_at": None,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise AnalysisRunStoreError("execution lease could not be released") from exc
        if document is None:
            raise AnalysisRunLeaseConflictError(
                "execution lease is not owned by this worker"
            )
        return _run_from_document(document)

    async def list_recoverable_runs(
        self,
        *,
        current_time: datetime,
        limit: int = 100,
    ) -> tuple[AnalysisRun, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        current_time = _as_utc(current_time)
        query = {
            "cancellation_requested": False,
            # `$ne` preserves claimability for any early Phase-8 documents
            # written before the explicit initialization gate was introduced.
            "inputs_ready": {"$ne": False},
            "$and": [
                {
                    "$or": [
                        {"expires_at": None},
                        {"expires_at": {"$gt": current_time}},
                    ]
                },
                {
                    "$or": [
                        {"status": AnalysisRunStatus.CREATED.value},
                        {
                            "status": AnalysisRunStatus.ACTIVE.value,
                            "$or": [
                                {"worker_id": None},
                                {"lease_expires_at": None},
                                {"lease_expires_at": {"$lte": current_time}},
                            ],
                        },
                    ]
                },
            ],
        }
        try:
            cursor = (
                self._db()[self.runs_collection_name]
                .find(query, {"_id": 0})
                .sort([("lease_expires_at", 1), ("created_at", 1), ("run_id", 1)])
                .limit(limit)
            )
            documents = await cursor.to_list(length=limit)
        except PyMongoError as exc:
            raise AnalysisRunStoreError(
                "recoverable analysis runs could not be listed"
            ) from exc
        return tuple(_run_from_document(document) for document in documents)

    async def list_abandoned_cancellations(
        self,
        *,
        current_time: datetime,
        limit: int = 100,
    ) -> tuple[AnalysisRun, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        current_time = _as_utc(current_time)
        query = {
            "cancellation_requested": True,
            "status": {
                "$in": [
                    AnalysisRunStatus.CREATED.value,
                    AnalysisRunStatus.ACTIVE.value,
                    AnalysisRunStatus.WAITING.value,
                ]
            },
            "$or": [
                {"worker_id": None},
                {"lease_expires_at": None},
                {"lease_expires_at": {"$lte": current_time}},
            ],
        }
        try:
            cursor = (
                self._db()[self.runs_collection_name]
                .find(query, {"_id": 0})
                .sort([("cancellation_requested_at", 1), ("run_id", 1)])
                .limit(limit)
            )
            documents = await cursor.to_list(length=limit)
        except PyMongoError as exc:
            raise AnalysisRunStoreError(
                "abandoned analysis cancellations could not be listed"
            ) from exc
        return tuple(_run_from_document(document) for document in documents)

    async def list_expirable_runs(
        self,
        *,
        current_time: datetime,
        limit: int = 100,
    ) -> tuple[AnalysisRun, ...]:
        """List deadline-elapsed runs whose execution lease is no longer live."""

        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        current_time = _as_utc(current_time)
        query = {
            "cancellation_requested": False,
            "status": {
                "$in": [
                    AnalysisRunStatus.CREATED.value,
                    AnalysisRunStatus.ACTIVE.value,
                    AnalysisRunStatus.WAITING.value,
                ]
            },
            "expires_at": {"$type": "date", "$lte": current_time},
            "$or": [
                {"worker_id": None},
                {"lease_expires_at": None},
                {"lease_expires_at": {"$lte": current_time}},
            ],
        }
        try:
            cursor = (
                self._db()[self.runs_collection_name]
                .find(query, {"_id": 0})
                .sort([("expires_at", 1), ("run_id", 1)])
                .limit(limit)
            )
            documents = await cursor.to_list(length=limit)
        except PyMongoError as exc:
            raise AnalysisRunStoreError(
                "expirable analysis runs could not be listed"
            ) from exc
        return tuple(_run_from_document(document) for document in documents)


__all__ = [
    "AnalysisRunConflictError",
    "AnalysisRunIdempotencyConflictError",
    "AnalysisRunLeaseConflictError",
    "AnalysisRunNotFoundError",
    "AnalysisRunStore",
    "AnalysisRunStoreError",
    "CreateRunResult",
    "MongoAnalysisRunStore",
    "RunMutationResult",
]
