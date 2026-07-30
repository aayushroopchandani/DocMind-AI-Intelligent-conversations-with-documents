from __future__ import annotations

import asyncio
import unittest
from datetime import timedelta
from typing import Any
from uuid import uuid4

from scripts.data_analysis_agent.analysis.state import AnalysisPhase
from scripts.data_analysis_agent.runtime.integration import (
    Phase7ExecutionCancelled,
    Phase7ExecutionResult,
    Phase7Progress,
)
from scripts.data_analysis_agent.runtime.models import (
    AnalysisEventType,
    AnalysisMode,
    AnalysisRun,
    AnalysisRunOutcome,
    AnalysisRunPhase,
    AnalysisRunStatus,
    CreateAnalysisRunRequest,
    DatasetHandle,
    DatasetVersionReference,
    SpreadsheetContext,
    WorkbookCellType,
    WorkbookRangeSnapshot,
    canonical_snapshot_hash,
)
from scripts.data_analysis_agent.runtime.repositories import (
    AnalysisRunIdempotencyConflictError,
    AnalysisRunLeaseConflictError,
    AnalysisRunStoreError,
    MongoAnalysisRunStore,
)
from scripts.data_analysis_agent.runtime.services.run_service import (
    AnalysisRunService,
    InvalidRunCursorError,
)
from scripts.data_analysis_agent.runtime.services.artifacts import (
    ArtifactFinalizationPendingError,
)
from scripts.data_analysis_agent.runtime.services.state_machine import (
    AnalysisRunStateMachine,
)
from scripts.data_analysis_agent.runtime.services.worker import (
    AnalysisWorkerConfig,
    DurableAnalysisWorker,
)
from scripts.data_analysis_agent.runtime.services.workbook_context import (
    ResolvedWorkbookContext,
    WorkbookContextError,
    WorkbookContextTooLargeError,
)
from tests.test_data_analysis_phase7_runtime_adapter import _dataset_handle
from tests.test_data_analysis_runtime_runs import _Clock, _Database, _new_run


class _DatasetCatalog:
    def __init__(self, handles: tuple[DatasetHandle, ...] = ()) -> None:
        self._handles = {
            (handle.dataset_id, handle.source_version): handle
            for handle in handles
        }
        self.calls: list[
            tuple[str, str, tuple[tuple[str, str], ...]]
        ] = []

    async def load_handles(
        self,
        *,
        user_id: str,
        workspace_id: str,
        versions: tuple[tuple[str, str], ...],
    ) -> tuple[DatasetHandle, ...]:
        self.calls.append((user_id, workspace_id, versions))
        return tuple(
            self._handles[identity]
            for identity in versions
            if identity in self._handles
        )


class _ResultAdapter:
    def __init__(
        self,
        result: Phase7ExecutionResult,
        *,
        progress: tuple[Phase7Progress, ...] = (),
    ) -> None:
        self._result = result
        self._progress = progress
        self.calls: list[
            tuple[AnalysisRun, tuple[DatasetHandle, ...]]
        ] = []

    async def execute(
        self,
        run: AnalysisRun,
        *,
        dataset_handles: tuple[DatasetHandle, ...],
        reporter: Any,
        is_cancelled: Any,
    ) -> Phase7ExecutionResult:
        self.calls.append((run, dataset_handles))
        if await is_cancelled():
            raise Phase7ExecutionCancelled()
        for item in self._progress:
            await reporter.emit(item)
        return self._result


class _CancellingAdapter:
    def __init__(self, machine: AnalysisRunStateMachine) -> None:
        self._machine = machine

    async def execute(
        self,
        run: AnalysisRun,
        *,
        dataset_handles: tuple[DatasetHandle, ...],
        reporter: Any,
        is_cancelled: Any,
    ) -> Phase7ExecutionResult:
        del dataset_handles, reporter
        await self._machine.request_cancellation(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        if await is_cancelled():
            raise Phase7ExecutionCancelled()
        raise AssertionError("the worker did not observe durable cancellation")


class _BlockingAdapter:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def execute(
        self,
        run: AnalysisRun,
        *,
        dataset_handles: tuple[DatasetHandle, ...],
        reporter: Any,
        is_cancelled: Any,
    ) -> Phase7ExecutionResult:
        del run, dataset_handles, reporter, is_cancelled
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("unreachable")


class _WorkbookContext:
    def __init__(self, dataset: DatasetHandle) -> None:
        self.dataset = dataset
        self.calls: list[dict[str, Any]] = []

    async def resolve(self, **kwargs: Any) -> ResolvedWorkbookContext:
        self.calls.append(kwargs)
        return ResolvedWorkbookContext(
            dataset_handles=(self.dataset,),
            workbook_artifact_version_id="workbook-version-1",
            dataset_artifact_version_ids=("dataset-version-1",),
        )


class _FailOnceWorkbookContext(_WorkbookContext):
    def __init__(self, dataset: DatasetHandle) -> None:
        super().__init__(dataset)
        self._failed = False

    async def resolve(self, **kwargs: Any) -> ResolvedWorkbookContext:
        self.calls.append(kwargs)
        if not self._failed:
            self._failed = True
            try:
                raise ArtifactFinalizationPendingError(
                    "pending-artifact-version"
                )
            except ArtifactFinalizationPendingError as exc:
                raise WorkbookContextError(
                    "temporary workbook sync failure"
                ) from exc
        return ResolvedWorkbookContext(
            dataset_handles=(self.dataset,),
            workbook_artifact_version_id="workbook-version-1",
            dataset_artifact_version_ids=("dataset-version-1",),
        )


class _PermanentWorkbookContext(_WorkbookContext):
    def __init__(
        self,
        dataset: DatasetHandle,
        *,
        before_error: Any = None,
    ) -> None:
        super().__init__(dataset)
        self._before_error = before_error

    async def resolve(self, **kwargs: Any) -> ResolvedWorkbookContext:
        self.calls.append(kwargs)
        if self._before_error is not None:
            await self._before_error()
        raise WorkbookContextTooLargeError(
            "oversized snapshot contained provider-secret-value"
        )


class _ConcurrentWorkbookContext(_WorkbookContext):
    def __init__(self, dataset: DatasetHandle) -> None:
        super().__init__(dataset)
        self._both_started = asyncio.Event()

    async def resolve(self, **kwargs: Any) -> ResolvedWorkbookContext:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            await asyncio.wait_for(self._both_started.wait(), timeout=1)
        else:
            self._both_started.set()
        return ResolvedWorkbookContext(
            dataset_handles=(self.dataset,),
            workbook_artifact_version_id="workbook-version-1",
            dataset_artifact_version_ids=("dataset-version-1",),
        )


class _MultiWorkbookContext(_WorkbookContext):
    def __init__(
        self,
        datasets: tuple[DatasetHandle, ...],
    ) -> None:
        super().__init__(datasets[0])
        self.datasets = datasets

    async def resolve(self, **kwargs: Any) -> ResolvedWorkbookContext:
        self.calls.append(kwargs)
        return ResolvedWorkbookContext(
            dataset_handles=self.datasets,
            workbook_artifact_version_id="workbook-version-1",
            dataset_artifact_version_ids=tuple(
                f"dataset-version-{index}"
                for index in range(1, len(self.datasets) + 1)
            ),
        )


def _prepared_result() -> Phase7ExecutionResult:
    return Phase7ExecutionResult(
        outcome=AnalysisRunOutcome.DATASETS_PREPARED,
        graph_phase=AnalysisPhase.PREPARED,
        final_dataset_ids=("normalized-dataset-1",),
        source_dataset_ids=("dataset-sheet-1",),
        prepared_dataset_count=1,
        total_input_rows=2,
        total_output_rows=2,
    )


def _clarification_result() -> Phase7ExecutionResult:
    return Phase7ExecutionResult(
        outcome=AnalysisRunOutcome.CLARIFICATION_REQUIRED,
        graph_phase=AnalysisPhase.ASSESSED,
    )


def _dataset_run(clock: _Clock, dataset: DatasetHandle) -> AnalysisRun:
    return _new_run(clock).model_copy(
        update={
            "selected_document_ids": (),
            "input_dataset_versions": (
                DatasetVersionReference(
                    dataset_id=dataset.dataset_id,
                    source_version=dataset.source_version,
                ),
            ),
        }
    )


def _worker(
    *,
    machine: AnalysisRunStateMachine,
    catalog: _DatasetCatalog,
    adapter: Any,
    worker_id: str,
) -> DurableAnalysisWorker:
    return DurableAnalysisWorker(
        state_machine=machine,
        dataset_catalog=catalog,
        adapter=adapter,
        config=AnalysisWorkerConfig(
            concurrency=1,
            poll_seconds=0.01,
            lease_seconds=30,
            renew_seconds=10,
            recovery_batch_size=10,
        ),
        worker_id=worker_id,
    )


def _spreadsheet_request() -> CreateAnalysisRunRequest:
    snapshot = WorkbookRangeSnapshot(
        range_a1="Sheet1!A1:B2",
        values=(("Revenue", "Region"), (60_000, "APAC")),
        formulas=((None, None), (None, None)),
        cell_types=(
            (WorkbookCellType.STRING, WorkbookCellType.STRING),
            (WorkbookCellType.NUMBER, WorkbookCellType.STRING),
        ),
        number_formats=(("General", "General"), ("#,##0", "General")),
        column_headers=("Revenue", "Region"),
        header_row_index=0,
        row_count=2,
        column_count=2,
    )
    context = SpreadsheetContext(
        workbook_id="workbook-1",
        workbook_name="Financial model",
        client_revision=12,
        worksheet_id="sheet-1",
        worksheet_name="Sheet1",
        selected_range="Sheet1!A1:B2",
        used_range="Sheet1!A1:B2",
        snapshot_range="Sheet1!A1:B2",
        snapshot_hash=canonical_snapshot_hash(snapshot),
        snapshot=snapshot,
    )
    return CreateAnalysisRunRequest(
        workspace_id="workspace-1",
        mode=AnalysisMode.ANALYSE,
        prompt="Filter revenue greater than 50,000.",
        spreadsheet_context=context,
    )


class DurableAnalysisWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.store = MongoAnalysisRunStore(_Database())
        self.machine = AnalysisRunStateMachine(
            self.store,
            clock=self.clock,
            maximum_lease_seconds=300,
        )

    async def _create(self, run: AnalysisRun) -> AnalysisRun:
        return (await self.machine.create_run(run=run)).run

    async def _events(self, run: AnalysisRun) -> tuple[AnalysisEventType, ...]:
        events = await self.store.list_events(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        return tuple(item.event_type for item in events)

    async def test_prepared_result_is_terminal_and_durably_evented(self) -> None:
        dataset = _dataset_handle()
        run = await self._create(_dataset_run(self.clock, dataset))
        adapter = _ResultAdapter(
            _prepared_result(),
            progress=(
                Phase7Progress(
                    event_type=AnalysisEventType.DATASET_PREPARED,
                    phase=AnalysisRunPhase.NORMALIZATION,
                    payload={
                        "dataset_id": "normalized-dataset-1",
                        "output_row_count": 2,
                    },
                    deduplication_key="dataset-prepared",
                ),
            ),
        )
        worker = _worker(
            machine=self.machine,
            catalog=_DatasetCatalog((dataset,)),
            adapter=adapter,
            worker_id="worker-success",
        )

        await worker._process_candidate(run)

        current = await self.machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertEqual(current.status, AnalysisRunStatus.SUCCEEDED)
        self.assertEqual(current.phase, AnalysisRunPhase.COMPLETED)
        self.assertEqual(
            current.outcome,
            AnalysisRunOutcome.DATASETS_PREPARED,
        )
        self.assertEqual(
            current.final_dataset_ids,
            ("normalized-dataset-1",),
        )
        self.assertIsNone(current.worker_id)
        self.assertEqual(
            await self._events(run),
            (
                AnalysisEventType.RUN_CREATED,
                AnalysisEventType.RUN_STARTED,
                AnalysisEventType.CONTEXT_RESOLUTION_STARTED,
                AnalysisEventType.DATASET_REGISTERED,
                AnalysisEventType.CONTEXT_RESOLVED,
                AnalysisEventType.DATASET_PREPARED,
                AnalysisEventType.RUN_COMPLETED,
            ),
        )
        self.assertEqual(adapter.calls[0][1], (dataset,))

    async def test_clarification_releases_lease_but_is_not_terminal(self) -> None:
        run = await self._create(
            _new_run(self.clock).model_copy(
                update={"selected_document_ids": ("2" * 64,)}
            )
        )
        worker = _worker(
            machine=self.machine,
            catalog=_DatasetCatalog(),
            adapter=_ResultAdapter(_clarification_result()),
            worker_id="worker-clarification",
        )

        await worker._process_candidate(run)

        current = await self.machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertEqual(current.status, AnalysisRunStatus.WAITING)
        self.assertEqual(
            current.outcome,
            AnalysisRunOutcome.CLARIFICATION_REQUIRED,
        )
        self.assertIsNone(current.worker_id)
        self.assertIsNone(current.completed_at)
        self.assertEqual(
            (await self._events(run))[-1],
            AnalysisEventType.CLARIFICATION_REQUIRED,
        )

    async def test_worker_observes_durable_cancellation(self) -> None:
        run = await self._create(
            _new_run(self.clock).model_copy(
                update={"selected_document_ids": ("2" * 64,)}
            )
        )
        worker = _worker(
            machine=self.machine,
            catalog=_DatasetCatalog(),
            adapter=_CancellingAdapter(self.machine),
            worker_id="worker-cancel",
        )

        await worker._process_candidate(run)

        current = await self.machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertEqual(current.status, AnalysisRunStatus.CANCELLED)
        self.assertEqual(current.outcome, AnalysisRunOutcome.CANCELLED)
        self.assertTrue(current.cancellation_requested)
        self.assertEqual(
            (await self._events(run))[-2:],
            (
                AnalysisEventType.CANCELLATION_REQUESTED,
                AnalysisEventType.RUN_CANCELLED,
            ),
        )

    async def test_missing_immutable_dataset_fails_before_adapter(self) -> None:
        dataset = _dataset_handle()
        run = await self._create(_dataset_run(self.clock, dataset))
        adapter = _ResultAdapter(_prepared_result())
        worker = _worker(
            machine=self.machine,
            catalog=_DatasetCatalog(),
            adapter=adapter,
            worker_id="worker-missing-input",
        )

        with self.assertLogs(
            "scripts.data_analysis_agent.runtime.services.worker",
            level="ERROR",
        ):
            await worker._process_candidate(run)

        current = await self.machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertEqual(current.status, AnalysisRunStatus.FAILED)
        self.assertEqual(current.outcome, AnalysisRunOutcome.FAILED)
        self.assertEqual(
            current.errors_summary[0].code,
            "analysis_runtime_failed",
        )
        self.assertFalse(adapter.calls)
        self.assertEqual(
            (await self._events(run))[-1],
            AnalysisEventType.RUN_FAILED,
        )

    async def test_polling_recovers_after_transient_store_failure(self) -> None:
        recovered = asyncio.Event()
        attempts = 0
        original = self.machine.list_abandoned_cancellations

        async def flaky_list(*, limit: int = 100) -> tuple[AnalysisRun, ...]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary database outage")
            recovered.set()
            return await original(limit=limit)

        self.machine.list_abandoned_cancellations = flaky_list  # type: ignore[method-assign]
        worker = _worker(
            machine=self.machine,
            catalog=_DatasetCatalog(),
            adapter=_ResultAdapter(_prepared_result()),
            worker_id="worker-poll-retry",
        )

        with self.assertLogs(
            "scripts.data_analysis_agent.runtime.services.worker",
            level="ERROR",
        ):
            await worker.start()
            await asyncio.wait_for(recovered.wait(), timeout=1)
            await worker.stop()

        self.assertGreaterEqual(attempts, 2)

    async def test_transient_lease_renewal_error_retries_before_deadline(
        self,
    ) -> None:
        run = await self._create(
            _new_run(self.clock).model_copy(
                update={"selected_document_ids": ("2" * 64,)}
            )
        )
        claimed = await self.machine.claim_execution(
            user_id=run.user_id,
            run_id=run.run_id,
            worker_id="worker-renew-retry",
            lease_seconds=3,
        )
        worker = DurableAnalysisWorker(
            state_machine=self.machine,
            dataset_catalog=_DatasetCatalog(),
            adapter=_ResultAdapter(_prepared_result()),
            config=AnalysisWorkerConfig(
                concurrency=1,
                poll_seconds=0.01,
                lease_seconds=3,
                renew_seconds=1,
                recovery_batch_size=10,
            ),
            worker_id="worker-renew-retry",
        )
        renewed = asyncio.Event()
        attempts = 0
        original = self.machine.renew_execution_lease

        async def flaky_renew(**kwargs: Any) -> AnalysisRun:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise AnalysisRunStoreError("temporary MongoDB outage")
            result = await original(**kwargs)
            renewed.set()
            return result

        self.machine.renew_execution_lease = flaky_renew  # type: ignore[method-assign]
        lease_lost = asyncio.Event()
        task = asyncio.create_task(
            worker._renew_lease(
                run=claimed.run,
                lease_attempt=claimed.run.lease_attempt,
                lease_lost=lease_lost,
            )
        )
        try:
            with self.assertLogs(
                "scripts.data_analysis_agent.runtime.services.worker",
                level="WARNING",
            ):
                await asyncio.wait_for(renewed.wait(), timeout=2)
            self.assertGreaterEqual(attempts, 2)
            self.assertFalse(lease_lost.is_set())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_confirmed_lease_loss_cancels_inflight_execution(
        self,
    ) -> None:
        run = await self._create(
            _new_run(self.clock).model_copy(
                update={"selected_document_ids": ("2" * 64,)}
            )
        )
        blocking = _BlockingAdapter()
        worker = _worker(
            machine=self.machine,
            catalog=_DatasetCatalog(),
            adapter=blocking,
            worker_id="worker-fenced",
        )

        async def lose_lease(**kwargs: Any) -> None:
            await blocking.started.wait()
            kwargs["lease_lost"].set()

        worker._renew_lease = lose_lease  # type: ignore[method-assign]

        await asyncio.wait_for(worker._process_candidate(run), timeout=1)

        self.assertTrue(blocking.cancelled.is_set())
        current = await self.machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertEqual(current.status, AnalysisRunStatus.ACTIVE)
        self.assertEqual(current.worker_id, "worker-fenced")

    async def test_worker_reconciles_elapsed_run_deadlines(self) -> None:
        initial = _new_run(
            self.clock,
            idempotency_key="worker-expiration-sweep",
            request_fingerprint="7" * 64,
        ).model_copy(
            update={"expires_at": self.clock() + timedelta(seconds=5)}
        )
        run = await self._create(initial)
        self.clock.advance(6)
        worker = _worker(
            machine=self.machine,
            catalog=_DatasetCatalog(),
            adapter=_ResultAdapter(_prepared_result()),
            worker_id="worker-expiration-sweep",
        )

        await worker._reconcile_expired_runs()

        expired = await self.machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertEqual(expired.status, AnalysisRunStatus.EXPIRED)
        self.assertEqual(
            (await self._events(run))[-1],
            AnalysisEventType.RUN_EXPIRED,
        )

    async def test_cancelled_task_releases_lease_and_next_worker_recovers(
        self,
    ) -> None:
        run = await self._create(
            _new_run(self.clock).model_copy(
                update={"selected_document_ids": ("2" * 64,)}
            )
        )
        blocking = _BlockingAdapter()
        first_worker = _worker(
            machine=self.machine,
            catalog=_DatasetCatalog(),
            adapter=blocking,
            worker_id="worker-first",
        )
        task = asyncio.create_task(first_worker._process_candidate(run))
        await asyncio.wait_for(blocking.started.wait(), timeout=1)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        released = await self.machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertEqual(released.status, AnalysisRunStatus.ACTIVE)
        self.assertIsNone(released.worker_id)
        self.assertIn(
            released.run_id,
            {
                item.run_id
                for item in await self.machine.list_recoverable_runs()
            },
        )

        second_worker = _worker(
            machine=self.machine,
            catalog=_DatasetCatalog(),
            adapter=_ResultAdapter(_prepared_result()),
            worker_id="worker-second",
        )
        await second_worker._process_candidate(released)

        recovered = await self.machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        self.assertEqual(recovered.status, AnalysisRunStatus.SUCCEEDED)
        self.assertEqual(recovered.lease_attempt, 2)
        self.assertIn(
            AnalysisEventType.RUN_RECOVERED,
            await self._events(run),
        )


class AnalysisRunServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.store = MongoAnalysisRunStore(_Database())
        self.machine = AnalysisRunStateMachine(
            self.store,
            clock=self.clock,
            maximum_lease_seconds=300,
        )

    async def test_pdf_run_has_a_bounded_queue_and_execution_deadline(
        self,
    ) -> None:
        service = AnalysisRunService(
            store=self.store,
            state_machine=self.machine,
            run_deadline_seconds=120,
            input_initialization_timeout_seconds=30,
        )
        request = CreateAnalysisRunRequest(
            workspace_id="workspace-1",
            mode=AnalysisMode.ANALYSE,
            prompt="Compare the reported revenue.",
            selected_document_ids=("2" * 64,),
        )

        created = await service.create_run(
            user_id="user-1",
            idempotency_key="pdf-deadline-1",
            request=request,
        )

        self.assertTrue(created.run.inputs_ready)
        self.assertIsNotNone(created.run.expires_at)
        assert created.run.expires_at is not None
        self.assertGreater(created.run.expires_at, created.run.updated_at)
        self.assertLessEqual(
            created.run.expires_at - created.run.created_at,
            timedelta(seconds=120),
        )

    async def test_spreadsheet_creation_is_durable_before_idempotent_replay(
        self,
    ) -> None:
        workbook = _WorkbookContext(_dataset_handle())
        service = AnalysisRunService(
            store=self.store,
            state_machine=self.machine,
            workbook_context=workbook,
        )
        request = _spreadsheet_request()

        created = await service.create_run(
            user_id="user-1",
            idempotency_key="spreadsheet-request-1",
            request=request,
        )
        replay = await service.create_run(
            user_id="user-1",
            idempotency_key="spreadsheet-request-1",
            request=request,
        )

        self.assertTrue(created.created)
        self.assertFalse(replay.created)
        self.assertEqual(replay.run.run_id, created.run.run_id)
        self.assertEqual(len(workbook.calls), 1)
        self.assertEqual(
            created.run.input_artifact_version_ids,
            ("workbook-version-1", "dataset-version-1"),
        )
        self.assertEqual(
            created.run.input_dataset_versions[0].dataset_id,
            "dataset-sheet-1",
        )
        self.assertTrue(created.run.inputs_ready)
        events = await self.store.list_events(
            user_id=created.run.user_id,
            run_id=created.run.run_id,
        )
        self.assertEqual(
            tuple(event.event_type for event in events),
            (
                AnalysisEventType.RUN_CREATED,
                AnalysisEventType.CONTEXT_RESOLVED,
            ),
        )
        self.assertEqual(
            events[1].payload,
            {
                "stage": "input_initialization",
                "artifact_version_count": 2,
                "dataset_version_count": 1,
            },
        )

        changed_request = request.model_copy(
            update={"prompt": "Use a different filter."}
        )
        with self.assertRaises(AnalysisRunIdempotencyConflictError):
            await service.create_run(
                user_id="user-1",
                idempotency_key="spreadsheet-request-1",
                request=changed_request,
            )
        self.assertEqual(len(workbook.calls), 1)

    async def test_failed_spreadsheet_sync_is_durable_and_retry_resumes_it(
        self,
    ) -> None:
        workbook = _FailOnceWorkbookContext(_dataset_handle())
        service = AnalysisRunService(
            store=self.store,
            state_machine=self.machine,
            workbook_context=workbook,
        )
        request = _spreadsheet_request()

        with self.assertRaises(WorkbookContextError):
            await service.create_run(
                user_id="user-1",
                idempotency_key="spreadsheet-retry-1",
                request=request,
            )

        pending = await self.store.get_run_by_idempotency_key(
            user_id="user-1",
            idempotency_key="spreadsheet-retry-1",
        )
        assert pending is not None
        self.assertFalse(pending.inputs_ready)
        self.assertIsNotNone(pending.expires_at)
        self.assertEqual(pending.input_artifact_version_ids, ())
        self.assertEqual(pending.input_dataset_versions, ())
        self.assertNotIn(
            pending.run_id,
            {
                item.run_id
                for item in await self.machine.list_recoverable_runs()
            },
        )
        with self.assertRaises(
            AnalysisRunLeaseConflictError
        ) as claim_error:
            await self.machine.claim_execution(
                user_id=pending.user_id,
                run_id=pending.run_id,
                worker_id="worker-too-early",
                lease_seconds=30,
            )
        self.assertIn("inputs are not ready", str(claim_error.exception))

        resumed = await service.create_run(
            user_id="user-1",
            idempotency_key="spreadsheet-retry-1",
            request=request,
        )

        self.assertFalse(resumed.created)
        self.assertEqual(resumed.run.run_id, pending.run_id)
        self.assertTrue(resumed.run.inputs_ready)
        self.assertIsNotNone(resumed.run.expires_at)
        assert resumed.run.expires_at is not None
        self.assertGreater(resumed.run.expires_at, resumed.run.updated_at)
        self.assertEqual(len(workbook.calls), 2)
        self.assertIn(
            resumed.run.run_id,
            {
                item.run_id
                for item in await self.machine.list_recoverable_runs()
            },
        )
        events = await self.store.list_events(
            user_id=resumed.run.user_id,
            run_id=resumed.run.run_id,
        )
        self.assertEqual(len(events), 2)

    async def test_permanent_input_error_is_durably_failed_once(self) -> None:
        workbook = _PermanentWorkbookContext(_dataset_handle())
        service = AnalysisRunService(
            store=self.store,
            state_machine=self.machine,
            workbook_context=workbook,
        )
        request = _spreadsheet_request()

        with self.assertRaises(WorkbookContextTooLargeError):
            await service.create_run(
                user_id="user-1",
                idempotency_key="spreadsheet-permanent-error",
                request=request,
            )

        failed = await self.store.get_run_by_idempotency_key(
            user_id="user-1",
            idempotency_key="spreadsheet-permanent-error",
        )
        assert failed is not None
        self.assertEqual(failed.status, AnalysisRunStatus.FAILED)
        self.assertEqual(failed.outcome, AnalysisRunOutcome.FAILED)
        self.assertFalse(failed.inputs_ready)
        self.assertEqual(
            failed.errors_summary[0].code,
            "input_context_too_large",
        )
        self.assertFalse(failed.errors_summary[0].retryable)
        events = await self.store.list_events(
            user_id=failed.user_id,
            run_id=failed.run_id,
        )
        self.assertEqual(
            tuple(event.event_type for event in events),
            (
                AnalysisEventType.RUN_CREATED,
                AnalysisEventType.RUN_FAILED,
            ),
        )
        self.assertNotIn(
            "provider-secret-value",
            events[-1].model_dump_json(),
        )

        replay = await service.create_run(
            user_id="user-1",
            idempotency_key="spreadsheet-permanent-error",
            request=request,
        )
        self.assertFalse(replay.created)
        self.assertEqual(replay.run.status, AnalysisRunStatus.FAILED)
        self.assertEqual(len(workbook.calls), 1)
        self.assertEqual(
            len(
                await self.store.list_events(
                    user_id=failed.user_id,
                    run_id=failed.run_id,
                )
            ),
            2,
        )

    async def test_cancellation_wins_permanent_input_failure_race(self) -> None:
        request = _spreadsheet_request()
        idempotency_key = "spreadsheet-cancel-race"

        async def request_cancellation() -> None:
            pending = await self.store.get_run_by_idempotency_key(
                user_id="user-1",
                idempotency_key=idempotency_key,
            )
            assert pending is not None
            await self.machine.request_cancellation(
                user_id=pending.user_id,
                run_id=pending.run_id,
            )

        workbook = _PermanentWorkbookContext(
            _dataset_handle(),
            before_error=request_cancellation,
        )
        service = AnalysisRunService(
            store=self.store,
            state_machine=self.machine,
            workbook_context=workbook,
        )

        result = await service.create_run(
            user_id="user-1",
            idempotency_key=idempotency_key,
            request=request,
        )

        self.assertEqual(result.run.status, AnalysisRunStatus.CANCELLED)
        self.assertEqual(result.run.outcome, AnalysisRunOutcome.CANCELLED)
        self.assertEqual(result.run.errors_summary, ())
        events = await self.store.list_events(
            user_id=result.run.user_id,
            run_id=result.run.run_id,
        )
        self.assertEqual(
            tuple(event.event_type for event in events),
            (
                AnalysisEventType.RUN_CREATED,
                AnalysisEventType.CANCELLATION_REQUESTED,
                AnalysisEventType.RUN_CANCELLED,
            ),
        )

    async def test_abandoned_pending_input_expires_before_retry(self) -> None:
        workbook = _FailOnceWorkbookContext(_dataset_handle())
        service = AnalysisRunService(
            store=self.store,
            state_machine=self.machine,
            workbook_context=workbook,
            input_initialization_timeout_seconds=30,
        )
        request = _spreadsheet_request()

        with self.assertRaises(WorkbookContextError):
            await service.create_run(
                user_id="user-1",
                idempotency_key="spreadsheet-expired-init",
                request=request,
            )
        pending = await self.store.get_run_by_idempotency_key(
            user_id="user-1",
            idempotency_key="spreadsheet-expired-init",
        )
        assert pending is not None
        assert pending.expires_at is not None
        self.clock.current = pending.expires_at + timedelta(seconds=1)

        expired = await service.create_run(
            user_id="user-1",
            idempotency_key="spreadsheet-expired-init",
            request=request,
        )

        self.assertFalse(expired.created)
        self.assertEqual(expired.run.status, AnalysisRunStatus.EXPIRED)
        self.assertEqual(expired.run.outcome, AnalysisRunOutcome.EXPIRED)
        self.assertEqual(len(workbook.calls), 1)
        self.assertEqual(
            (
                await self.store.list_events(
                    user_id=expired.run.user_id,
                    run_id=expired.run.run_id,
                )
            )[-1].event_type,
            AnalysisEventType.RUN_EXPIRED,
        )

    async def test_detected_workbook_tables_are_all_attached_to_the_run(
        self,
    ) -> None:
        first = _dataset_handle()
        second = first.model_copy(
            update={
                "dataset_id": "dataset-sheet-2",
                "source_version": "e" * 64,
            }
        )
        workbook = _MultiWorkbookContext((first, second))
        service = AnalysisRunService(
            store=self.store,
            state_machine=self.machine,
            workbook_context=workbook,
        )

        created = await service.create_run(
            user_id="user-1",
            idempotency_key="spreadsheet-multi-table-1",
            request=_spreadsheet_request(),
        )

        self.assertEqual(
            tuple(
                reference.dataset_id
                for reference in created.run.input_dataset_versions
            ),
            ("dataset-sheet-1", "dataset-sheet-2"),
        )
        self.assertEqual(
            created.run.input_artifact_version_ids,
            (
                "workbook-version-1",
                "dataset-version-1",
                "dataset-version-2",
            ),
        )
        events = await self.store.list_events(
            user_id=created.run.user_id,
            run_id=created.run.run_id,
        )
        self.assertEqual(events[-1].payload["dataset_version_count"], 2)

    async def test_concurrent_duplicate_creation_publishes_one_ready_run(
        self,
    ) -> None:
        workbook = _ConcurrentWorkbookContext(_dataset_handle())
        service = AnalysisRunService(
            store=self.store,
            state_machine=self.machine,
            workbook_context=workbook,
        )
        request = _spreadsheet_request()

        results = await asyncio.gather(
            service.create_run(
                user_id="user-1",
                idempotency_key="spreadsheet-concurrent-1",
                request=request,
            ),
            service.create_run(
                user_id="user-1",
                idempotency_key="spreadsheet-concurrent-1",
                request=request,
            ),
        )

        self.assertEqual({item.run.run_id for item in results}, {results[0].run.run_id})
        self.assertEqual(sorted(item.created for item in results), [False, True])
        self.assertTrue(all(item.run.inputs_ready for item in results))
        self.assertEqual(len(workbook.calls), 2)
        database = self.store._db()
        self.assertEqual(len(database["analysis_runs"].documents), 1)
        events = await self.store.list_events(
            user_id="user-1",
            run_id=results[0].run.run_id,
        )
        self.assertEqual(
            tuple(event.event_type for event in events),
            (
                AnalysisEventType.RUN_CREATED,
                AnalysisEventType.CONTEXT_RESOLVED,
            ),
        )

    async def test_run_history_cursor_is_stable_and_non_overlapping(self) -> None:
        service = AnalysisRunService(
            store=self.store,
            state_machine=self.machine,
        )
        created: list[AnalysisRun] = []
        for index in range(3):
            timestamp = self.clock() + timedelta(minutes=index)
            initial = _new_run(
                self.clock,
                idempotency_key=f"history-request-{index}",
                request_fingerprint=f"{index + 1}" * 64,
            ).model_copy(
                update={
                    "run_id": str(uuid4()),
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
            )
            created.append(
                (await self.machine.create_run(run=initial)).run
            )

        first = await service.list_runs(user_id="user-1", limit=2)
        second = await service.list_runs(
            user_id="user-1",
            cursor=first.next_cursor,
            limit=2,
        )

        self.assertIsNotNone(first.next_cursor)
        self.assertIsNone(second.next_cursor)
        self.assertEqual(len(first.items), 2)
        self.assertEqual(len(second.items), 1)
        self.assertFalse(
            {item.run_id for item in first.items}
            & {item.run_id for item in second.items}
        )
        self.assertEqual(
            {
                item.run_id
                for item in (*first.items, *second.items)
            },
            {item.run_id for item in created},
        )

        with self.assertRaises(InvalidRunCursorError):
            await service.list_runs(
                user_id="user-1",
                cursor="not-a-valid-cursor",
            )


if __name__ == "__main__":
    unittest.main()
