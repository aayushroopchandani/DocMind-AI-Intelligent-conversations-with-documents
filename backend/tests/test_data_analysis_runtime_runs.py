from __future__ import annotations

import asyncio
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from pymongo.errors import DuplicateKeyError, OperationFailure

from scripts.data_analysis_agent.runtime.models import (
    AnalysisEventType,
    AnalysisMode,
    AnalysisRun,
    AnalysisRunOutcome,
    AnalysisRunPhase,
    AnalysisRunStatus,
    DatasetVersionReference,
)
from scripts.data_analysis_agent.runtime.repositories import (
    AnalysisRunConflictError,
    AnalysisRunIdempotencyConflictError,
    AnalysisRunLeaseConflictError,
    AnalysisRunStoreError,
    MongoAnalysisRunStore,
)
from scripts.data_analysis_agent.runtime.services.state_machine import (
    AnalysisRunStateMachine,
    InvalidAnalysisRunTransition,
)


def _operator_matches(*, actual: Any, exists: bool, expression: dict[str, Any]) -> bool:
    for operator, expected in expression.items():
        if operator == "$gt" and not (exists and actual > expected):
            return False
        if operator == "$gte" and not (exists and actual >= expected):
            return False
        if operator == "$lt" and not (exists and actual < expected):
            return False
        if operator == "$lte" and not (exists and actual <= expected):
            return False
        if operator == "$in":
            if isinstance(actual, (list, tuple)):
                if not set(actual).intersection(expected):
                    return False
            elif actual not in expected:
                return False
        if operator == "$nin" and actual in expected:
            return False
        if operator == "$ne" and actual == expected:
            return False
        if operator == "$exists" and exists is not bool(expected):
            return False
        if operator == "$type":
            if expected == "date" and not isinstance(actual, datetime):
                return False
    return True


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if key == "$and":
            if not all(_matches(document, item) for item in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(document, item) for item in expected):
                return False
            continue

        actual: Any = document
        exists = True
        for segment in key.split("."):
            if isinstance(actual, dict) and segment in actual:
                actual = actual[segment]
                continue
            if (
                isinstance(actual, (list, tuple))
                and segment.isdigit()
                and int(segment) < len(actual)
            ):
                actual = actual[int(segment)]
                continue
            else:
                exists = False
                actual = None
                break
        if isinstance(expected, dict) and any(
            str(operator).startswith("$") for operator in expected
        ):
            if not _operator_matches(
                actual=actual,
                exists=exists,
                expression=expected,
            ):
                return False
        elif actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = [deepcopy(item) for item in documents]

    def sort(self, field_or_fields: Any, direction: int | None = None) -> "_Cursor":
        fields = (
            [(field_or_fields, direction)]
            if isinstance(field_or_fields, str)
            else list(field_or_fields)
        )
        for field, order in reversed(fields):
            self._documents.sort(
                key=lambda item: (
                    item.get(field) is not None,
                    item.get(field),
                ),
                reverse=order == -1,
            )
        return self

    def limit(self, limit: int) -> "_Cursor":
        self._documents = self._documents[:limit]
        return self

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        return deepcopy(self._documents[:length])


class _Collection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.documents: list[dict[str, Any]] = []
        self.sessions: list[Any] = []
        self.fail_next_insert = False

    def _check_unique(self, candidate: dict[str, Any]) -> None:
        for existing in self.documents:
            if self.name == "analysis_runs":
                duplicate_run = (
                    existing["user_id"],
                    existing["run_id"],
                ) == (
                    candidate["user_id"],
                    candidate["run_id"],
                )
                duplicate_request = (
                    existing["user_id"],
                    existing["idempotency_key"],
                ) == (
                    candidate["user_id"],
                    candidate["idempotency_key"],
                )
                if duplicate_run or duplicate_request:
                    raise DuplicateKeyError("duplicate analysis run")
            elif self.name == "analysis_run_events":
                duplicate_sequence = (
                    existing["user_id"],
                    existing["run_id"],
                    existing["sequence"],
                ) == (
                    candidate["user_id"],
                    candidate["run_id"],
                    candidate["sequence"],
                )
                deduplication_key = candidate.get("deduplication_key")
                duplicate_command = deduplication_key is not None and (
                    existing["user_id"],
                    existing["run_id"],
                    existing.get("deduplication_key"),
                ) == (
                    candidate["user_id"],
                    candidate["run_id"],
                    deduplication_key,
                )
                if duplicate_sequence or duplicate_command:
                    raise DuplicateKeyError("duplicate analysis event")
            elif self.name == "analysis_plans":
                if (
                    existing["user_id"],
                    existing["run_id"],
                    existing["revision"],
                ) == (
                    candidate["user_id"],
                    candidate["run_id"],
                    candidate["revision"],
                ) or (
                    existing["user_id"],
                    existing["run_id"],
                    existing["plan_id"],
                ) == (
                    candidate["user_id"],
                    candidate["run_id"],
                    candidate["plan_id"],
                ):
                    raise DuplicateKeyError("duplicate analysis plan")
                if (
                    existing.get("reservation_active") is True
                    and candidate.get("reservation_active") is True
                    and existing["user_id"] == candidate["user_id"]
                    and existing["workspace_id"] == candidate["workspace_id"]
                    and set(existing.get("write_target_keys", ())).intersection(
                        candidate.get("write_target_keys", ())
                    )
                ):
                    raise DuplicateKeyError("duplicate write reservation")
            elif self.name == "analysis_patch_proposals" and (
                existing["user_id"],
                existing["run_id"],
                existing["patch_id"],
            ) == (
                candidate["user_id"],
                candidate["run_id"],
                candidate["patch_id"],
            ):
                raise DuplicateKeyError("duplicate patch proposal")

    async def insert_one(
        self,
        document: dict[str, Any],
        *,
        session: Any = None,
    ) -> SimpleNamespace:
        self.sessions.append(session)
        if self.fail_next_insert:
            self.fail_next_insert = False
            raise OperationFailure("simulated insert failure")
        candidate = deepcopy(document)
        self._check_unique(candidate)
        self.documents.append(candidate)
        return SimpleNamespace(inserted_id=str(uuid4()))

    async def find_one(
        self,
        query: dict[str, Any],
        *_args: Any,
        session: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        self.sessions.append(session)
        matches = [
            deepcopy(document)
            for document in self.documents
            if _matches(document, query)
        ]
        for field, order in reversed(kwargs.get("sort") or ()):
            matches.sort(
                key=lambda item: item.get(field),
                reverse=order == -1,
            )
        return matches[0] if matches else None

    def find(
        self,
        query: dict[str, Any],
        *_args: Any,
        **_kwargs: Any,
    ) -> _Cursor:
        return _Cursor(
            [
                document
                for document in self.documents
                if _matches(document, query)
            ]
        )

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        session: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        self.sessions.append(session)
        for index, document in enumerate(self.documents):
            if not _matches(document, query):
                continue
            updated = deepcopy(document)
            updated.update(deepcopy(update.get("$set", {})))
            self.documents[index] = updated
            return deepcopy(updated)
        return None

    async def update_many(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        session: Any = None,
    ) -> SimpleNamespace:
        self.sessions.append(session)
        count = 0
        for index, document in enumerate(self.documents):
            if not _matches(document, query):
                continue
            updated = deepcopy(document)
            updated.update(deepcopy(update.get("$set", {})))
            self.documents[index] = updated
            count += 1
        return SimpleNamespace(modified_count=count)


class _Session:
    def __init__(self, database: "_Database") -> None:
        self._database = database

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def with_transaction(self, callback: Any) -> Any:
        async with self._database.transaction_lock:
            snapshots = {
                name: deepcopy(collection.documents)
                for name, collection in self._database.collections.items()
            }
            try:
                return await callback(self)
            except BaseException:
                for name, documents in snapshots.items():
                    self._database.collections[name].documents = documents
                raise


class _Client:
    def __init__(self, database: "_Database") -> None:
        self._database = database
        self.started_sessions = 0

    async def start_session(self) -> _Session:
        self.started_sessions += 1
        return _Session(self._database)


class _Database:
    def __init__(self) -> None:
        self.transaction_lock = asyncio.Lock()
        self.collections = {
            "analysis_runs": _Collection("analysis_runs"),
            "analysis_run_events": _Collection("analysis_run_events"),
            "analysis_plans": _Collection("analysis_plans"),
            "analysis_patch_proposals": _Collection(
                "analysis_patch_proposals"
            ),
        }
        self.client = _Client(self)

    def __getitem__(self, name: str) -> _Collection:
        return self.collections[name]


class _Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def _new_run(
    clock: _Clock,
    *,
    user_id: str = "user-1",
    workspace_id: str = "workspace-1",
    idempotency_key: str = "request-key-1",
    request_fingerprint: str = "a" * 64,
) -> AnalysisRun:
    return AnalysisRun(
        run_id=str(uuid4()),
        user_id=user_id,
        workspace_id=workspace_id,
        chat_id=workspace_id,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        mode=AnalysisMode.ANALYSE,
        prompt="Compare revenue by year.",
        created_at=clock(),
        updated_at=clock(),
    )


class DurableRunRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.database = _Database()
        self.clock = _Clock()
        self.store = MongoAnalysisRunStore(self.database)
        self.machine = AnalysisRunStateMachine(
            self.store,
            clock=self.clock,
            maximum_lease_seconds=300,
        )

    async def _create(self, **kwargs: Any) -> AnalysisRun:
        result = await self.machine.create_run(run=_new_run(self.clock, **kwargs))
        self.assertTrue(result.created)
        return result.run

    async def test_create_is_transactional_versioned_and_replayable(self) -> None:
        initial = _new_run(self.clock)

        created = await self.machine.create_run(run=initial)

        self.assertEqual(created.run.version, 1)
        self.assertEqual(created.run.last_event_sequence, 1)
        self.assertEqual(created.event.sequence, 1)
        self.assertEqual(created.event.event_type, AnalysisEventType.RUN_CREATED)
        self.assertEqual(self.database.client.started_sessions, 1)
        self.assertTrue(
            all(
                session is not None
                for collection in self.database.collections.values()
                for session in collection.sessions
            )
        )

        events = await self.store.list_events(
            user_id=initial.user_id,
            run_id=initial.run_id,
        )
        self.assertEqual(events, (created.event,))

    async def test_same_idempotency_key_replays_only_same_request(self) -> None:
        initial = _new_run(self.clock)
        first = await self.machine.create_run(run=initial)
        same_request = initial.model_copy(update={"run_id": str(uuid4())})

        replay = await self.machine.create_run(run=same_request)

        self.assertFalse(replay.created)
        self.assertEqual(replay.run.run_id, first.run.run_id)
        self.assertEqual(
            len(self.database["analysis_run_events"].documents),
            1,
        )

        different_request = same_request.model_copy(
            update={
                "run_id": str(uuid4()),
                "request_fingerprint": "b" * 64,
            }
        )
        with self.assertRaises(AnalysisRunIdempotencyConflictError):
            await self.machine.create_run(run=different_request)

    async def test_tenant_scope_applies_to_runs_and_event_replay(self) -> None:
        run = await self._create()

        self.assertIsNone(
            await self.store.get_run(user_id="user-2", run_id=run.run_id)
        )
        self.assertEqual(
            await self.store.list_events(
                user_id="user-2",
                run_id=run.run_id,
            ),
            (),
        )

    async def test_progress_events_use_cas_ordering_and_deduplicate(self) -> None:
        run = await self._create()
        first = await self.machine.record_event(
            user_id=run.user_id,
            run_id=run.run_id,
            event_type=AnalysisEventType.CONTEXT_RESOLUTION_STARTED,
            expected_version=run.version,
            deduplication_key="context-started",
        )
        replay = await self.machine.record_event(
            user_id=run.user_id,
            run_id=run.run_id,
            event_type=AnalysisEventType.CONTEXT_RESOLUTION_STARTED,
            expected_version=run.version,
            deduplication_key="context-started",
        )

        self.assertTrue(first.changed)
        self.assertFalse(replay.changed)
        self.assertEqual(first.event, replay.event)
        self.assertEqual(first.run.version, 2)
        self.assertEqual(first.event.sequence, 2)
        self.assertEqual(
            len(self.database["analysis_run_events"].documents),
            2,
        )

        with self.assertRaises(AnalysisRunConflictError):
            await self.machine.record_event(
                user_id=run.user_id,
                run_id=run.run_id,
                event_type=AnalysisEventType.CONTEXT_RESOLVED,
                expected_version=run.version,
            )

    async def test_event_insert_failure_rolls_back_run_transition(self) -> None:
        run = await self._create()
        self.database["analysis_run_events"].fail_next_insert = True

        with self.assertRaises(AnalysisRunStoreError):
            await self.machine.record_event(
                user_id=run.user_id,
                run_id=run.run_id,
                event_type=AnalysisEventType.CONTEXT_RESOLVED,
                expected_version=run.version,
            )

        current = await self.store.get_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertEqual(current.version, run.version)
        self.assertEqual(
            current.last_event_sequence,
            run.last_event_sequence,
        )
        self.assertEqual(
            len(self.database["analysis_run_events"].documents),
            1,
        )

    async def test_input_references_and_ready_gate_commit_with_one_event(
        self,
    ) -> None:
        run = await self._create(
            idempotency_key="pending-input-request",
            request_fingerprint="f" * 64,
        )
        pending = run.model_copy(update={"inputs_ready": False})
        self.database["analysis_runs"].documents[0] = pending.model_dump(
            mode="python"
        )
        dataset_versions = (
            DatasetVersionReference(
                dataset_id="sheet-dataset-1",
                source_version="d" * 64,
            ),
        )
        self.database["analysis_run_events"].fail_next_insert = True

        with self.assertRaises(AnalysisRunStoreError):
            await self.machine.complete_input_initialization(
                user_id=pending.user_id,
                run_id=pending.run_id,
                active_artifact_id="workbook-1",
                artifact_version_ids=(
                    "workbook-version-1",
                    "dataset-version-1",
                ),
                dataset_versions=dataset_versions,
            )

        unchanged = await self.machine.require_run(
            user_id=pending.user_id,
            run_id=pending.run_id,
        )
        self.assertFalse(unchanged.inputs_ready)
        self.assertEqual(unchanged.input_artifact_version_ids, ())
        self.assertEqual(unchanged.input_dataset_versions, ())
        self.assertEqual(unchanged.last_event_sequence, 1)

        completed = await self.machine.complete_input_initialization(
            user_id=pending.user_id,
            run_id=pending.run_id,
            active_artifact_id="workbook-1",
            artifact_version_ids=(
                "workbook-version-1",
                "dataset-version-1",
            ),
            dataset_versions=dataset_versions,
        )

        self.assertTrue(completed.run.inputs_ready)
        self.assertEqual(
            completed.run.input_dataset_versions,
            dataset_versions,
        )
        assert completed.event is not None
        self.assertEqual(completed.event.sequence, 2)
        self.assertEqual(
            completed.event.event_type,
            AnalysisEventType.CONTEXT_RESOLVED,
        )

    async def test_event_replay_is_strictly_after_cursor_and_bounded(self) -> None:
        run = await self._create()
        current = run
        for index, event_type in enumerate(
            (
                AnalysisEventType.CONTEXT_RESOLUTION_STARTED,
                AnalysisEventType.CONTEXT_RESOLVED,
                AnalysisEventType.REQUIREMENTS_STARTED,
            ),
            start=1,
        ):
            result = await self.machine.record_event(
                user_id=run.user_id,
                run_id=run.run_id,
                event_type=event_type,
                expected_version=current.version,
                deduplication_key=f"event-{index}",
            )
            current = result.run

        replay = await self.store.list_events(
            user_id=run.user_id,
            run_id=run.run_id,
            after_sequence=2,
            limit=1,
        )
        self.assertEqual([event.sequence for event in replay], [3])

    async def test_concurrent_writers_cannot_allocate_the_same_sequence(self) -> None:
        run = await self._create()

        results = await asyncio.gather(
            self.machine.record_event(
                user_id=run.user_id,
                run_id=run.run_id,
                event_type=AnalysisEventType.CONTEXT_RESOLUTION_STARTED,
                expected_version=run.version,
                deduplication_key="writer-a",
            ),
            self.machine.record_event(
                user_id=run.user_id,
                run_id=run.run_id,
                event_type=AnalysisEventType.REQUIREMENTS_STARTED,
                expected_version=run.version,
                deduplication_key="writer-b",
            ),
            return_exceptions=True,
        )

        successes = [
            result for result in results if not isinstance(result, BaseException)
        ]
        conflicts = [
            result
            for result in results
            if isinstance(result, AnalysisRunConflictError)
        ]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(conflicts), 1)

        current = await self.store.get_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        failed_index = next(
            index
            for index, result in enumerate(results)
            if isinstance(result, AnalysisRunConflictError)
        )
        retry_event_type = (
            AnalysisEventType.CONTEXT_RESOLUTION_STARTED
            if failed_index == 0
            else AnalysisEventType.REQUIREMENTS_STARTED
        )
        retry = await self.machine.record_event(
            user_id=run.user_id,
            run_id=run.run_id,
            event_type=retry_event_type,
            expected_version=current.version,
            deduplication_key=("writer-a" if failed_index == 0 else "writer-b"),
        )
        self.assertEqual(retry.event.sequence, 3)

        events = await self.store.list_events(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertEqual([event.sequence for event in events], [1, 2, 3])

    async def test_created_run_can_be_claimed_once(self) -> None:
        run = await self._create()

        claimed = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-1",
            lease_seconds=30,
            expected_version=run.version,
        )

        self.assertTrue(claimed.changed)
        self.assertEqual(claimed.run.status, AnalysisRunStatus.ACTIVE)
        self.assertEqual(claimed.run.worker_id, "worker-1")
        self.assertEqual(claimed.run.lease_attempt, 1)
        self.assertEqual(claimed.event.event_type, AnalysisEventType.RUN_STARTED)
        self.assertEqual(claimed.run.version, 2)
        self.assertEqual(claimed.run.last_event_sequence, 2)

        same_worker = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-1",
            lease_seconds=30,
        )
        self.assertFalse(same_worker.changed)

        with self.assertRaises(AnalysisRunLeaseConflictError):
            await self.machine.claim_execution(
                user_id=run.user_id,
                run_id=run.run_id,
                worker_id="worker-2",
                lease_seconds=30,
            )

    async def test_lifecycle_timestamps_tolerate_cross_node_clock_skew(
        self,
    ) -> None:
        run = await self._create()
        behind_clock = _Clock()
        behind_clock.current = self.clock.current - timedelta(seconds=1)
        behind_node = AnalysisRunStateMachine(
            self.store,
            clock=behind_clock,
            maximum_lease_seconds=300,
        )

        claimed = await behind_node.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-behind",
            lease_seconds=30,
            expected_version=run.version,
        )
        progressed = await behind_node.record_event(
            user_id=run.user_id,
            run_id=run.run_id,
            event_type=AnalysisEventType.CONTEXT_RESOLUTION_STARTED,
            worker_id="worker-behind",
            lease_attempt=claimed.run.lease_attempt,
        )
        requested = await behind_node.request_cancellation(
            user_id=run.user_id,
            run_id=run.run_id,
            expected_version=progressed.run.version,
        )
        cancelled = await behind_node.transition(
            user_id=run.user_id,
            run_id=run.run_id,
            target_status=AnalysisRunStatus.CANCELLED,
            target_phase=AnalysisRunPhase.COMPLETED,
            outcome=AnalysisRunOutcome.CANCELLED,
            event_type=AnalysisEventType.RUN_CANCELLED,
            worker_id="worker-behind",
            lease_attempt=claimed.run.lease_attempt,
        )

        self.assertGreaterEqual(claimed.run.updated_at, run.updated_at)
        self.assertGreaterEqual(progressed.run.updated_at, claimed.run.updated_at)
        self.assertGreaterEqual(requested.run.updated_at, progressed.run.updated_at)
        self.assertGreaterEqual(
            cancelled.run.completed_at,
            requested.run.updated_at,
        )

    async def test_expired_lease_is_recovered_with_new_attempt(self) -> None:
        run = await self._create()
        first = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-1",
            lease_seconds=10,
        )
        self.clock.advance(11)

        recovered = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-2",
            lease_seconds=20,
        )

        self.assertEqual(recovered.run.worker_id, "worker-2")
        self.assertEqual(recovered.run.lease_attempt, 2)
        self.assertEqual(
            recovered.event.event_type,
            AnalysisEventType.RUN_RECOVERED,
        )
        self.assertGreater(recovered.run.version, first.run.version)

    async def test_lease_renewal_and_release_require_current_owner(self) -> None:
        run = await self._create()
        claimed = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-1",
            lease_seconds=20,
        )
        original_expiry = claimed.run.lease_expires_at
        self.clock.advance(5)

        renewed = await self.machine.renew_execution_lease(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-1",
            lease_attempt=claimed.run.lease_attempt,
            lease_seconds=30,
        )
        self.assertGreater(renewed.lease_expires_at, original_expiry)
        self.assertEqual(renewed.version, claimed.run.version)

        with self.assertRaises(AnalysisRunLeaseConflictError):
            await self.machine.release_execution_lease(
                user_id=run.user_id,
                run_id=run.run_id,
                worker_id="worker-2",
                lease_attempt=claimed.run.lease_attempt,
            )

        released = await self.machine.release_execution_lease(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-1",
            lease_attempt=claimed.run.lease_attempt,
        )
        self.assertIsNone(released.worker_id)
        self.assertIsNone(released.lease_expires_at)

    async def test_expired_lease_cannot_be_renewed(self) -> None:
        run = await self._create()
        claimed = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-1",
            lease_seconds=5,
        )
        self.clock.advance(6)

        with self.assertRaises(AnalysisRunLeaseConflictError):
            await self.machine.renew_execution_lease(
                user_id=run.user_id,
                run_id=run.run_id,
                worker_id="worker-1",
                lease_attempt=claimed.run.lease_attempt,
                lease_seconds=10,
            )

    async def test_elapsed_run_expires_with_atomic_terminal_event(self) -> None:
        initial = _new_run(
            self.clock,
            idempotency_key="run-deadline-1",
            request_fingerprint="e" * 64,
        ).model_copy(
            update={"expires_at": self.clock() + timedelta(seconds=10)}
        )
        run = (await self.machine.create_run(run=initial)).run
        self.clock.advance(11)
        self.database["analysis_run_events"].fail_next_insert = True

        with self.assertRaises(AnalysisRunStoreError):
            await self.machine.expire_run(
                user_id=run.user_id,
                run_id=run.run_id,
            )

        unchanged = await self.machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertEqual(unchanged.status, AnalysisRunStatus.CREATED)
        self.assertEqual(unchanged.last_event_sequence, 1)

        swept = await self.machine.expire_due_runs()

        self.assertEqual(len(swept), 1)
        expired = swept[0].run
        self.assertEqual(expired.status, AnalysisRunStatus.EXPIRED)
        self.assertEqual(expired.phase, AnalysisRunPhase.COMPLETED)
        self.assertEqual(expired.outcome, AnalysisRunOutcome.EXPIRED)
        self.assertIsNotNone(expired.completed_at)
        self.assertIsNone(expired.worker_id)
        assert swept[0].event is not None
        self.assertEqual(
            swept[0].event.event_type,
            AnalysisEventType.RUN_EXPIRED,
        )
        self.assertEqual(
            swept[0].event.payload,
            {"reason": "deadline_elapsed"},
        )
        replay = await self.machine.expire_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertFalse(replay.changed)
        self.assertEqual(replay.event, swept[0].event)
        self.assertEqual(await self.machine.expire_due_runs(), ())

    async def test_expiration_waits_for_live_lease_and_blocks_renewal(
        self,
    ) -> None:
        initial = _new_run(
            self.clock,
            idempotency_key="leased-run-deadline",
            request_fingerprint="9" * 64,
        ).model_copy(
            update={"expires_at": self.clock() + timedelta(seconds=5)}
        )
        run = (await self.machine.create_run(run=initial)).run
        claimed = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-deadline",
            lease_seconds=10,
        )
        self.assertEqual(
            claimed.run.lease_expires_at,
            claimed.run.expires_at,
        )
        self.clock.advance(4)

        self.assertEqual(await self.machine.expire_due_runs(), ())
        self.clock.advance(2)
        with self.assertRaises(AnalysisRunLeaseConflictError):
            await self.machine.renew_execution_lease(
                user_id=run.user_id,
                run_id=run.run_id,
                worker_id="worker-deadline",
                lease_attempt=claimed.run.lease_attempt,
                lease_seconds=10,
            )

        swept = await self.machine.expire_due_runs()

        self.assertEqual(len(swept), 1)
        self.assertEqual(swept[0].run.status, AnalysisRunStatus.EXPIRED)
        self.assertIsNone(swept[0].run.worker_id)
        self.assertIsNone(swept[0].run.lease_expires_at)

    async def test_expiration_sweep_ignores_runs_without_deadline(self) -> None:
        run = await self._create(
            idempotency_key="no-run-deadline",
            request_fingerprint="8" * 64,
        )
        self.clock.advance(10_000)

        self.assertEqual(await self.machine.expire_due_runs(), ())
        current = await self.machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertEqual(current.status, AnalysisRunStatus.CREATED)

    async def test_stale_lease_attempt_cannot_release_recovered_execution(
        self,
    ) -> None:
        run = await self._create()
        first = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-reused",
            lease_seconds=5,
        )
        self.clock.advance(6)
        recovered = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-reused",
            lease_seconds=20,
        )

        with self.assertRaises(AnalysisRunLeaseConflictError):
            await self.machine.release_execution_lease(
                user_id=run.user_id,
                run_id=run.run_id,
                worker_id="worker-reused",
                lease_attempt=first.run.lease_attempt,
            )

        current = await self.store.get_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertEqual(current.lease_attempt, recovered.run.lease_attempt)
        self.assertEqual(current.worker_id, "worker-reused")

    async def test_cancellation_request_is_durable_and_idempotent(self) -> None:
        run = await self._create()

        first = await self.machine.request_cancellation(
            user_id=run.user_id,
            run_id=run.run_id,
            expected_version=run.version,
        )
        second = await self.machine.request_cancellation(
            user_id=run.user_id,
            run_id=run.run_id,
        )

        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertTrue(second.run.cancellation_requested)
        self.assertIsNotNone(second.run.cancellation_requested_at)
        self.assertEqual(
            sum(
                event["event_type"]
                == AnalysisEventType.CANCELLATION_REQUESTED
                for event in self.database["analysis_run_events"].documents
            ),
            1,
        )

    async def test_pause_checkpoints_same_run_and_resume_requeues_it(self) -> None:
        run = await self._create()
        claimed = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-pause",
            lease_seconds=30,
        )
        requested = await self.machine.request_pause(
            user_id=run.user_id,
            run_id=run.run_id,
            expected_version=claimed.run.version,
        )

        self.assertTrue(requested.run.pause_requested)
        with self.assertRaises(InvalidAnalysisRunTransition):
            await self.machine.record_event(
                user_id=run.user_id,
                run_id=run.run_id,
                event_type=AnalysisEventType.DATASET_PREPARED,
                worker_id="worker-pause",
                lease_attempt=claimed.run.lease_attempt,
            )

        paused = await self.machine.finalize_requested_pause(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-pause",
            lease_attempt=claimed.run.lease_attempt,
            last_completed_step_id="normalization",
        )
        self.assertEqual(paused.run.run_id, run.run_id)
        self.assertEqual(paused.run.status, AnalysisRunStatus.PAUSED)
        self.assertIsNotNone(paused.run.checkpoint_id)
        self.assertEqual(paused.run.last_completed_step_id, "normalization")
        self.assertIsNone(paused.run.worker_id)
        self.assertIsNone(paused.run.expires_at)
        self.assertFalse(paused.run.pause_requested)

        resumed = await self.machine.resume_paused_run(
            user_id=run.user_id,
            run_id=run.run_id,
            execution_expires_at=self.clock() + timedelta(hours=1),
            expected_version=paused.run.version,
        )
        self.assertEqual(resumed.run.run_id, run.run_id)
        self.assertEqual(resumed.run.status, AnalysisRunStatus.CREATED)
        self.assertEqual(resumed.run.resume_count, 1)
        self.assertIsNone(resumed.run.paused_at)
        self.assertEqual(
            [
                event["event_type"]
                for event in self.database["analysis_run_events"].documents
            ][-3:],
            [
                AnalysisEventType.PAUSE_REQUESTED,
                AnalysisEventType.RUN_PAUSED,
                AnalysisEventType.RUN_RESUMED,
            ],
        )

    async def test_cancelled_paused_run_stays_terminal(self) -> None:
        run = await self._create()
        await self.machine.request_pause(user_id=run.user_id, run_id=run.run_id)
        paused = await self.machine.finalize_requested_pause(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        requested = await self.machine.request_cancellation(
            user_id=run.user_id,
            run_id=run.run_id,
            expected_version=paused.run.version,
        )
        cancelled = await self.machine.finalize_requested_cancellation(
            user_id=run.user_id,
            run_id=run.run_id,
        )

        self.assertTrue(requested.run.cancellation_requested)
        self.assertEqual(cancelled.run.status, AnalysisRunStatus.CANCELLED)
        self.assertIsNone(cancelled.run.paused_at)
        with self.assertRaises(InvalidAnalysisRunTransition):
            await self.machine.resume_paused_run(
                user_id=run.user_id,
                run_id=run.run_id,
                execution_expires_at=self.clock() + timedelta(hours=1),
            )

    async def test_cancellation_request_wins_over_successful_completion(self) -> None:
        run = await self._create()
        claimed = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-1",
            lease_seconds=30,
        )
        requested = await self.machine.request_cancellation(
            user_id=run.user_id,
            run_id=run.run_id,
        )

        with self.assertRaises(InvalidAnalysisRunTransition):
            await self.machine.transition(
                user_id=run.user_id,
                run_id=run.run_id,
                target_status=AnalysisRunStatus.SUCCEEDED,
                target_phase=AnalysisRunPhase.COMPLETED,
                outcome=AnalysisRunOutcome.DATASETS_PREPARED,
                event_type=AnalysisEventType.RUN_COMPLETED,
                expected_version=requested.run.version,
                worker_id="worker-1",
                lease_attempt=claimed.run.lease_attempt,
            )

        cancelled = await self.machine.transition(
            user_id=run.user_id,
            run_id=run.run_id,
            target_status=AnalysisRunStatus.CANCELLED,
            target_phase=AnalysisRunPhase.COMPLETED,
            outcome=AnalysisRunOutcome.CANCELLED,
            event_type=AnalysisEventType.RUN_CANCELLED,
            expected_version=requested.run.version,
            worker_id="worker-1",
            lease_attempt=claimed.run.lease_attempt,
        )
        self.assertEqual(cancelled.run.status, AnalysisRunStatus.CANCELLED)
        self.assertIsNone(cancelled.run.worker_id)

    async def test_deduplication_key_cannot_be_reused_with_new_payload(self) -> None:
        run = await self._create()
        await self.machine.record_event(
            user_id=run.user_id,
            run_id=run.run_id,
            event_type=AnalysisEventType.CONTEXT_RESOLVED,
            payload={"dataset_count": 1},
            deduplication_key="context-result",
        )

        with self.assertRaises(InvalidAnalysisRunTransition):
            await self.machine.record_event(
                user_id=run.user_id,
                run_id=run.run_id,
                event_type=AnalysisEventType.CONTEXT_RESOLVED,
                payload={"dataset_count": 2},
                deduplication_key="context-result",
            )

    async def test_successful_terminal_transition_clears_lease(self) -> None:
        run = await self._create()
        claimed = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-1",
            lease_seconds=30,
        )

        completed = await self.machine.transition(
            user_id=run.user_id,
            run_id=run.run_id,
            target_status=AnalysisRunStatus.SUCCEEDED,
            target_phase=AnalysisRunPhase.COMPLETED,
            outcome=AnalysisRunOutcome.DATASETS_PREPARED,
            event_type=AnalysisEventType.RUN_COMPLETED,
            expected_version=claimed.run.version,
            deduplication_key="run-completed",
            worker_id="worker-1",
            lease_attempt=claimed.run.lease_attempt,
        )

        self.assertEqual(completed.run.status, AnalysisRunStatus.SUCCEEDED)
        self.assertIsNone(completed.run.worker_id)
        self.assertIsNotNone(completed.run.completed_at)
        self.assertEqual(
            completed.event.event_type,
            AnalysisEventType.RUN_COMPLETED,
        )

        replay = await self.machine.transition(
            user_id=run.user_id,
            run_id=run.run_id,
            target_status=AnalysisRunStatus.SUCCEEDED,
            target_phase=AnalysisRunPhase.COMPLETED,
            outcome=AnalysisRunOutcome.DATASETS_PREPARED,
            event_type=AnalysisEventType.RUN_COMPLETED,
            deduplication_key="run-completed",
            worker_id="worker-1",
        )
        self.assertFalse(replay.changed)

        with self.assertRaises(InvalidAnalysisRunTransition):
            await self.machine.record_event(
                user_id=run.user_id,
                run_id=run.run_id,
                event_type=AnalysisEventType.DATASET_PREPARED,
            )

    async def test_state_machine_rejects_backward_or_mislabelled_transitions(
        self,
    ) -> None:
        run = await self._create()
        claimed = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-1",
            lease_seconds=30,
        )
        progressed = await self.machine.record_event(
            user_id=run.user_id,
            run_id=run.run_id,
            event_type=AnalysisEventType.DATASETS_PROFILED,
            phase=AnalysisRunPhase.NORMALIZATION,
            worker_id="worker-1",
            lease_attempt=claimed.run.lease_attempt,
        )

        with self.assertRaises(InvalidAnalysisRunTransition):
            await self.machine.record_event(
                user_id=run.user_id,
                run_id=run.run_id,
                event_type=AnalysisEventType.REQUIREMENTS_COMPLETED,
                phase=AnalysisRunPhase.REQUIREMENTS,
                worker_id="worker-1",
                lease_attempt=claimed.run.lease_attempt,
            )

        with self.assertRaises(InvalidAnalysisRunTransition):
            await self.machine.transition(
                user_id=run.user_id,
                run_id=run.run_id,
                target_status=AnalysisRunStatus.FAILED,
                target_phase=AnalysisRunPhase.COMPLETED,
                outcome=AnalysisRunOutcome.FAILED,
                event_type=AnalysisEventType.RUN_COMPLETED,
                expected_version=progressed.run.version,
                worker_id="worker-1",
                lease_attempt=claimed.run.lease_attempt,
            )

        self.assertEqual(claimed.run.worker_id, "worker-1")

    async def test_recovery_scan_excludes_live_cancelled_waiting_and_expired_runs(
        self,
    ) -> None:
        unclaimed = await self._create(
            idempotency_key="request-unclaimed",
            request_fingerprint="1" * 64,
        )
        live = await self._create(
            idempotency_key="request-live",
            request_fingerprint="2" * 64,
        )
        await self.machine.claim_execution(
            user_id=live.user_id,
            run_id=live.run_id,
            worker_id="worker-live",
            lease_seconds=30,
        )
        cancelled = await self._create(
            idempotency_key="request-cancelled",
            request_fingerprint="3" * 64,
        )
        await self.machine.request_cancellation(
            user_id=cancelled.user_id,
            run_id=cancelled.run_id,
        )

        recoverable = await self.machine.list_recoverable_runs()
        recoverable_ids = {item.run_id for item in recoverable}

        self.assertIn(unclaimed.run_id, recoverable_ids)
        self.assertNotIn(live.run_id, recoverable_ids)
        self.assertNotIn(cancelled.run_id, recoverable_ids)


if __name__ == "__main__":
    unittest.main()
