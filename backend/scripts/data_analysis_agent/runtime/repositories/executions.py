"""Durable persistence for execution records (Phase 9.8.1, 9.8.5).

Two properties this store exists to provide:

*Exactly one execution per key.* `reserve` is a conditional insert on
`(user_id, execution_key)`. Duplicate queue delivery therefore finds the
existing record instead of starting a second execution, and a already-succeeded
record is returned as a cache hit rather than recomputed.

*Only the current worker may publish.* Every mutation is a compare-and-set on
`(execution_id, version, fencing_token)`. A recovered worker that lost its lease
can still finish computing — nothing can stop a process mid-flight — but its
publication fails, so a stale attempt can never overwrite a newer one.

*A run can be asked what it executed.* Writers address an execution by its key,
because that is what makes execution idempotent. Readers only know a run, so
`get_for_run` resolves the newest attempt for one. Both lookups are single-
document and index-backed; neither scans.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from db.mongodb import get_db

from ..models.executions import (
    AnalysisExecution,
    CheckpointRecord,
    ExecutionMetrics,
    ExecutionStatus,
    ResultArtifacts,
    StageRecord,
    StageStatus,
)
from ..models.plans import PlanColumn


class ExecutionRepositoryError(RuntimeError):
    """Execution persistence failed."""


class ExecutionNotFoundError(ExecutionRepositoryError):
    """The tenant-scoped execution does not exist."""


class ExecutionFencedError(ExecutionRepositoryError):
    """A stale worker attempted to publish over a newer attempt."""


def _utc(value: datetime) -> datetime:
    return (
        value.astimezone(timezone.utc)
        if value.tzinfo is not None
        else value.replace(tzinfo=timezone.utc)
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionRepository(Protocol):
    async def reserve(self, execution: AnalysisExecution) -> AnalysisExecution: ...

    async def get_by_key(
        self,
        *,
        user_id: str,
        execution_key: str,
    ) -> AnalysisExecution | None: ...

    async def get_by_id(
        self,
        *,
        user_id: str,
        execution_id: str,
    ) -> AnalysisExecution | None: ...

    async def get_for_run(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> AnalysisExecution | None: ...

    async def start(
        self,
        *,
        execution: AnalysisExecution,
        worker_id: str,
        fencing_token: int,
    ) -> AnalysisExecution: ...

    async def record_stage(
        self,
        *,
        execution: AnalysisExecution,
        stage: StageRecord,
    ) -> AnalysisExecution: ...

    async def publish(
        self,
        *,
        execution: AnalysisExecution,
        content_hash: str,
        columns: tuple[PlanColumn, ...],
        artifacts: ResultArtifacts,
        metrics: ExecutionMetrics,
    ) -> AnalysisExecution: ...

    async def fail(
        self,
        *,
        execution: AnalysisExecution,
        code: str,
        message: str,
        cancelled: bool = False,
    ) -> AnalysisExecution: ...

    async def pause(
        self,
        *,
        execution: AnalysisExecution,
    ) -> AnalysisExecution: ...


class MongoExecutionRepository:
    collection_name = "analysis_executions"

    def __init__(self, database: Any | None = None) -> None:
        self._database = database

    def _db(self) -> Any:
        return self._database if self._database is not None else get_db()

    async def reserve(self, execution: AnalysisExecution) -> AnalysisExecution:
        """Claim the execution key, or return whoever claimed it first."""

        existing = await self.get_by_key(
            user_id=execution.user_id,
            execution_key=execution.execution_key,
        )
        if existing is not None:
            return existing
        try:
            await self._db()[self.collection_name].insert_one(
                execution.model_dump(mode="python")
            )
        except DuplicateKeyError:
            # Another worker won the race; its record is authoritative.
            claimed = await self.get_by_key(
                user_id=execution.user_id,
                execution_key=execution.execution_key,
            )
            if claimed is None:  # pragma: no cover - index guarantees one exists
                raise ExecutionRepositoryError(
                    "execution key is claimed but unreadable"
                ) from None
            return claimed
        except PyMongoError as error:
            raise ExecutionRepositoryError(
                "execution record could not be reserved"
            ) from error
        return execution

    async def get_by_key(
        self,
        *,
        user_id: str,
        execution_key: str,
    ) -> AnalysisExecution | None:
        try:
            document = await self._db()[self.collection_name].find_one(
                {"user_id": user_id, "execution_key": execution_key}
            )
        except PyMongoError as error:
            raise ExecutionRepositoryError(
                "execution record could not be read"
            ) from error
        return AnalysisExecution.model_validate(document) if document else None

    async def get_by_id(
        self,
        *,
        user_id: str,
        execution_id: str,
    ) -> AnalysisExecution | None:
        try:
            document = await self._db()[self.collection_name].find_one(
                {"user_id": user_id, "execution_id": execution_id}
            )
        except PyMongoError as error:
            raise ExecutionRepositoryError(
                "execution record could not be read"
            ) from error
        return AnalysisExecution.model_validate(document) if document else None

    async def get_for_run(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> AnalysisExecution | None:
        """Return the newest execution attempt for one tenant-scoped run.

        A run can execute more than once — a linked retry re-plans, and a
        re-planned recipe has a different execution key. The newest record is
        the one describing what the run currently holds.
        """

        try:
            document = await self._db()[self.collection_name].find_one(
                {"user_id": user_id, "run_id": run_id},
                sort=[("created_at", DESCENDING)],
            )
        except PyMongoError as error:
            raise ExecutionRepositoryError(
                "execution record could not be read"
            ) from error
        return AnalysisExecution.model_validate(document) if document else None

    async def start(
        self,
        *,
        execution: AnalysisExecution,
        worker_id: str,
        fencing_token: int,
    ) -> AnalysisExecution:
        if execution.fencing_token > fencing_token:
            raise ExecutionFencedError(
                "a newer attempt already owns this execution"
            )
        return await self._mutate(
            execution,
            {
                "status": ExecutionStatus.RUNNING.value,
                "worker_id": worker_id,
                "fencing_token": fencing_token,
                "started_at": execution.started_at or utc_now(),
            },
        )

    async def record_stage(
        self,
        *,
        execution: AnalysisExecution,
        stage: StageRecord,
    ) -> AnalysisExecution:
        stages = [
            item for item in execution.stages if item.stage_id != stage.stage_id
        ]
        stages.append(stage)
        return await self._mutate(
            execution,
            {
                "stages": [item.model_dump(mode="python") for item in stages],
                "current_stage_id": stage.stage_id,
            },
        )

    async def publish(
        self,
        *,
        execution: AnalysisExecution,
        content_hash: str,
        columns: tuple[PlanColumn, ...],
        artifacts: ResultArtifacts,
        metrics: ExecutionMetrics,
    ) -> AnalysisExecution:
        """Commit the result. Fails if a newer attempt took over."""

        now = utc_now()
        return await self._mutate(
            execution,
            {
                "status": ExecutionStatus.SUCCEEDED.value,
                "result_content_hash": content_hash,
                "result_columns": [
                    column.model_dump(mode="python") for column in columns
                ],
                "artifacts": artifacts.model_dump(mode="python"),
                "metrics": metrics.model_dump(mode="python"),
                "started_at": execution.started_at or now,
                "finished_at": now,
                "current_stage_id": None,
            },
        )

    async def fail(
        self,
        *,
        execution: AnalysisExecution,
        code: str,
        message: str,
        cancelled: bool = False,
    ) -> AnalysisExecution:
        now = utc_now()
        return await self._mutate(
            execution,
            {
                "status": (
                    ExecutionStatus.CANCELLED.value
                    if cancelled
                    else ExecutionStatus.FAILED.value
                ),
                "failure_code": code,
                "failure_message": message[:1_000],
                "started_at": execution.started_at or now,
                "finished_at": now,
            },
        )

    async def pause(self, *, execution: AnalysisExecution) -> AnalysisExecution:
        """Persist progress and park the execution at its last checkpoint."""

        return await self._mutate(
            execution,
            {"status": ExecutionStatus.PAUSED.value},
        )

    async def _mutate(
        self,
        execution: AnalysisExecution,
        updates: dict[str, Any],
    ) -> AnalysisExecution:
        """Apply a compare-and-set update guarded by version and fencing token."""

        payload = {**updates, "version": execution.version + 1, "updated_at": utc_now()}
        try:
            document = await self._db()[
                self.collection_name
            ].find_one_and_update(
                {
                    "execution_id": execution.execution_id,
                    "user_id": execution.user_id,
                    "version": execution.version,
                    # A worker whose token was superseded cannot write.
                    "fencing_token": {"$lte": execution.fencing_token},
                },
                {"$set": payload},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as error:
            raise ExecutionRepositoryError(
                "execution record could not be updated"
            ) from error
        if document is None:
            raise ExecutionFencedError(
                "the execution changed under this worker; its write was rejected"
            )
        return AnalysisExecution.model_validate(document)


class InMemoryExecutionRepository:
    """Process-local store with the same semantics as the Mongo one.

    Used by tests and single-process development. It enforces the same key
    uniqueness and the same fencing rules, so a test that passes here is
    testing the real contract rather than a weaker one.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, AnalysisExecution] = {}
        self._by_key: dict[tuple[str, str], str] = {}
        # Secondary index, maintained on reserve so `get_for_run` is a lookup
        # rather than a scan — the same shape the Mongo index gives the real
        # store. Reservation order is creation order, so the last entry is the
        # newest attempt.
        self._by_run: dict[tuple[str, str], list[str]] = {}

    async def reserve(self, execution: AnalysisExecution) -> AnalysisExecution:
        key = (execution.user_id, execution.execution_key)
        existing_id = self._by_key.get(key)
        if existing_id is not None:
            return self._by_id[existing_id]
        self._by_key[key] = execution.execution_id
        self._by_id[execution.execution_id] = execution
        self._by_run.setdefault(
            (execution.user_id, execution.run_id), []
        ).append(execution.execution_id)
        return execution

    async def get_by_key(
        self,
        *,
        user_id: str,
        execution_key: str,
    ) -> AnalysisExecution | None:
        execution_id = self._by_key.get((user_id, execution_key))
        return self._by_id.get(execution_id) if execution_id else None

    async def get_by_id(
        self,
        *,
        user_id: str,
        execution_id: str,
    ) -> AnalysisExecution | None:
        execution = self._by_id.get(execution_id)
        return execution if execution and execution.user_id == user_id else None

    async def get_for_run(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> AnalysisExecution | None:
        identifiers = self._by_run.get((user_id, run_id))
        if not identifiers:
            return None
        # Newest by `created_at`, not by reservation order. The two normally
        # agree, but the Mongo store sorts on the field and this one claims the
        # same semantics — so it sorts on the field too.
        candidates = [
            execution
            for execution in (self._by_id.get(item) for item in identifiers)
            if execution is not None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda execution: execution.created_at)

    async def start(
        self,
        *,
        execution: AnalysisExecution,
        worker_id: str,
        fencing_token: int,
    ) -> AnalysisExecution:
        current = self._current(execution)
        if current.fencing_token > fencing_token:
            raise ExecutionFencedError(
                "a newer attempt already owns this execution"
            )
        return self._apply(
            execution,
            status=ExecutionStatus.RUNNING,
            worker_id=worker_id,
            fencing_token=fencing_token,
            started_at=current.started_at or utc_now(),
        )

    async def record_stage(
        self,
        *,
        execution: AnalysisExecution,
        stage: StageRecord,
    ) -> AnalysisExecution:
        current = self._current(execution)
        stages = [item for item in current.stages if item.stage_id != stage.stage_id]
        stages.append(stage)
        return self._apply(
            execution,
            stages=tuple(stages),
            current_stage_id=stage.stage_id,
        )

    async def publish(
        self,
        *,
        execution: AnalysisExecution,
        content_hash: str,
        columns: tuple[PlanColumn, ...],
        artifacts: ResultArtifacts,
        metrics: ExecutionMetrics,
    ) -> AnalysisExecution:
        now = utc_now()
        current = self._current(execution)
        return self._apply(
            execution,
            status=ExecutionStatus.SUCCEEDED,
            result_content_hash=content_hash,
            result_columns=columns,
            artifacts=artifacts,
            metrics=metrics,
            started_at=current.started_at or now,
            finished_at=now,
            current_stage_id=None,
        )

    async def fail(
        self,
        *,
        execution: AnalysisExecution,
        code: str,
        message: str,
        cancelled: bool = False,
    ) -> AnalysisExecution:
        now = utc_now()
        current = self._current(execution)
        return self._apply(
            execution,
            status=(
                ExecutionStatus.CANCELLED if cancelled else ExecutionStatus.FAILED
            ),
            failure_code=code,
            failure_message=message[:1_000],
            started_at=current.started_at or now,
            finished_at=now,
        )

    async def pause(self, *, execution: AnalysisExecution) -> AnalysisExecution:
        return self._apply(execution, status=ExecutionStatus.PAUSED)

    def _current(self, execution: AnalysisExecution) -> AnalysisExecution:
        stored = self._by_id.get(execution.execution_id)
        if stored is None:
            raise ExecutionNotFoundError("execution record does not exist")
        return stored

    def _apply(
        self,
        execution: AnalysisExecution,
        **updates: Any,
    ) -> AnalysisExecution:
        current = self._current(execution)
        if current.version != execution.version:
            raise ExecutionFencedError(
                "the execution changed under this worker; its write was rejected"
            )
        if current.fencing_token > execution.fencing_token:
            raise ExecutionFencedError(
                "a newer attempt owns this execution"
            )
        updated = current.model_copy(
            update={
                **updates,
                "version": current.version + 1,
                "updated_at": utc_now(),
            }
        )
        # Re-validate so the in-memory path cannot accept a state the Mongo
        # path would reject on read.
        updated = AnalysisExecution.model_validate(
            updated.model_dump(mode="python")
        )
        self._by_id[updated.execution_id] = updated
        return updated


def completed_stage(
    *,
    stage_id: str,
    step_ids: tuple[str, ...],
    input_rows: int,
    output_rows: int,
    output_columns: int,
    duration_ms: float = 0.0,
    checkpoint: CheckpointRecord | None = None,
) -> StageRecord:
    """Build the record for a stage that finished successfully."""

    return StageRecord(
        stage_id=stage_id,
        step_ids=step_ids,
        status=StageStatus.COMPLETED,
        input_rows=input_rows,
        output_rows=output_rows,
        output_columns=output_columns,
        duration_ms=duration_ms,
        checkpoint=checkpoint,
    )


__all__ = [
    "ExecutionFencedError",
    "InMemoryExecutionRepository",
    "ExecutionNotFoundError",
    "ExecutionRepository",
    "ExecutionRepositoryError",
    "MongoExecutionRepository",
    "completed_stage",
    "utc_now",
]
