"""Run lifecycle across both execution-engine states.

Phase 9.1.2 routes a validated plan into the execution queue. The engine that
drains that queue lands in Phase 9.4, so the runtime must behave correctly in
both deployments: complete at `plan_ready` while no engine exists, and queue
once one does. These tests pin both halves, plus the success-path metric that
the original Phase 9 commit dropped.
"""

from __future__ import annotations

import unittest
from uuid import uuid4

from scripts.data_analysis_agent.runtime.execution import (
    ExecutionAdmission,
    ExecutionFailureCode,
    InputResolutionError,
    NativeExecutionService,
    ResolvedInput,
)
from scripts.data_analysis_agent.runtime.execution.idempotency import (
    dataset_content_signature,
)
from scripts.data_analysis_agent.runtime.models.capabilities import (
    ExecutorCapabilities,
)
from scripts.data_analysis_agent.runtime.models.events import AnalysisEventType
from scripts.data_analysis_agent.runtime.models.plans import (
    ApprovalPolicy,
    PlanApprovalCommand,
    PlanApprovalStatus,
    PlanDiagnostics,
    build_analysis_plan,
)
from scripts.data_analysis_agent.runtime.models.runs import (
    AnalysisRunOutcome,
    AnalysisRunPhase,
    AnalysisRunStatus,
    RunApprovalStatus,
)
from scripts.data_analysis_agent.runtime.observability.metrics import (
    analysis_metrics,
)
from scripts.data_analysis_agent.runtime.planning.contracts import (
    PlanValidationReport,
    PlanningExecutionResult,
    PlanningOutcome,
)
from scripts.data_analysis_agent.runtime.planning.service import (
    AnalysisPlanningService,
)
from scripts.data_analysis_agent.runtime.repositories.plans import (
    MongoAnalysisPlanRepository,
)
from scripts.data_analysis_agent.runtime.repositories.runs import (
    MongoAnalysisRunStore,
)
from scripts.data_analysis_agent.runtime.services.state_machine import (
    AnalysisRunStateMachine,
)
from scripts.data_analysis_agent.runtime.services.worker import (
    AnalysisWorkerConfig,
    DurableAnalysisWorker,
)
from scripts.data_analysis_agent.runtime.integration.contracts import (
    Phase7PlanningArtifacts,
)

from tests.test_data_analysis_phase8_planning import (
    NATIVE_ENGINE_READY,
    _USER_ID,
    _Clock,
    _ContextBuilder,
    _Database,
    _DatasetCatalog,
    _Planner,
    _ResultAdapter,
    _WorkerPlanningService,
    _approval_plan,
    _context,
    _dataset_handle,
    _dataset_run,
    _prepared_result,
    _proposal,
    _requirements,
    _run,
    _service_draft,
)


class WorkerAdmissionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def _run_worker(
        self,
        admission: ExecutionAdmission,
        worker_id: str,
        *,
        execution_service=None,
    ):
        database = _Database()
        clock = _Clock()
        store = MongoAnalysisRunStore(database)
        state_machine = AnalysisRunStateMachine(
            store,
            clock=clock,
            maximum_lease_seconds=300,
        )
        dataset = _dataset_handle()
        run = (
            await state_machine.create_run(run=_dataset_run(clock, dataset))
        ).run
        context = _context(run_id=run.run_id, mode=run.mode)
        plan = build_analysis_plan(
            draft=_service_draft(context, _proposal(with_write=False)),
            user_id=run.user_id,
            workspace_id=run.workspace_id,
            revision=1,
            approval_policy=ApprovalPolicy(
                plan_approval_required=False,
                final_patch_approval_required=False,
                auto_execute_read_only=True,
            ),
            diagnostics=PlanDiagnostics(generation_attempt=1, repair_count=0),
            model="test-planner",
        )
        phase7_result = _prepared_result().model_copy(
            update={
                "planning_artifacts": Phase7PlanningArtifacts.model_construct(
                    requirements=_requirements(),
                    dataset_profiles=object(),
                    normalization=object(),
                )
            }
        )
        worker = DurableAnalysisWorker(
            state_machine=state_machine,
            dataset_catalog=_DatasetCatalog((dataset,)),
            adapter=_ResultAdapter(phase7_result),
            planning_service=_WorkerPlanningService(
                PlanningExecutionResult(
                    outcome=PlanningOutcome.PLAN_READY,
                    plan=plan,
                    admission=admission,
                    reports=(PlanValidationReport(),),
                )
            ),
            execution_service=execution_service,
            config=AnalysisWorkerConfig(
                concurrency=1,
                poll_seconds=0.01,
                lease_seconds=30,
                renew_seconds=10,
                recovery_batch_size=10,
            ),
            worker_id=worker_id,
        )

        await worker._process_candidate(run)

        current = await state_machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        events = await store.list_events(
            user_id=run.user_id,
            run_id=run.run_id,
            limit=100,
        )
        return current, events

    async def test_plan_completes_the_run_when_no_engine_is_installed(self) -> None:
        current, events = await self._run_worker(
            ExecutionAdmission.PLAN_ONLY,
            "worker-plan-only",
        )

        self.assertEqual(current.status, AnalysisRunStatus.SUCCEEDED)
        self.assertEqual(current.phase, AnalysisRunPhase.COMPLETED)
        self.assertEqual(current.outcome, AnalysisRunOutcome.PLAN_READY)
        self.assertIsNotNone(current.completed_at)
        self.assertEqual(events[-1].event_type, AnalysisEventType.PLAN_READY)

    async def test_plan_queues_the_run_when_the_engine_is_installed(self) -> None:
        current, events = await self._run_worker(
            ExecutionAdmission.QUEUE,
            "worker-queue",
        )

        self.assertEqual(current.status, AnalysisRunStatus.WAITING)
        self.assertEqual(current.phase, AnalysisRunPhase.EXECUTION)
        self.assertEqual(current.outcome, AnalysisRunOutcome.QUEUED_FOR_EXECUTION)
        self.assertIsNone(current.completed_at)
        self.assertEqual(events[-1].event_type, AnalysisEventType.EXECUTION_QUEUED)

    async def test_a_successful_run_records_an_outcome_metric(self) -> None:
        before = dict(analysis_metrics.snapshot().get("run_outcomes", {}))

        await self._run_worker(ExecutionAdmission.PLAN_ONLY, "worker-metric-plan")
        after_plan = dict(analysis_metrics.snapshot().get("run_outcomes", {}))
        await self._run_worker(ExecutionAdmission.QUEUE, "worker-metric-queue")
        after_queue = dict(analysis_metrics.snapshot().get("run_outcomes", {}))

        self.assertEqual(
            after_plan.get("plan_ready", 0),
            before.get("plan_ready", 0) + 1,
        )
        self.assertEqual(
            after_queue.get("queued_for_execution", 0),
            after_plan.get("queued_for_execution", 0) + 1,
        )


class WorkerNativeExecutionTests(unittest.IsolatedAsyncioTestCase):
    """The queue must actually drain once a native engine is installed."""

    async def test_a_queued_run_executes_and_completes(self) -> None:
        harness = WorkerAdmissionLifecycleTests()
        service = NativeExecutionService(
            resolver=_ExecutionStubResolver(),
            capabilities=ExecutorCapabilities(native_execution_ready=True),
        )

        current, events = await harness._run_worker(
            ExecutionAdmission.QUEUE,
            "worker-native-execute",
            execution_service=service,
        )

        self.assertEqual(current.status, AnalysisRunStatus.SUCCEEDED)
        self.assertEqual(current.phase, AnalysisRunPhase.COMPLETED)
        self.assertEqual(current.outcome, AnalysisRunOutcome.COMPLETED)
        self.assertIsNotNone(current.completed_at)
        types = [event.event_type for event in events]
        self.assertIn(AnalysisEventType.EXECUTION_QUEUED, types)
        self.assertIn(AnalysisEventType.EXECUTION_STARTED, types)
        self.assertEqual(types[-1], AnalysisEventType.RUN_COMPLETED)
        payload = events[-1].payload
        self.assertTrue(payload["content_hash"])
        self.assertIn("polars", payload["engine_version"])

    async def test_an_execution_failure_fails_the_run_with_its_code(self) -> None:
        harness = WorkerAdmissionLifecycleTests()
        service = NativeExecutionService(
            resolver=_ExecutionFailingResolver(),
            capabilities=ExecutorCapabilities(native_execution_ready=True),
        )

        current, events = await harness._run_worker(
            ExecutionAdmission.QUEUE,
            "worker-native-failure",
            execution_service=service,
        )

        self.assertEqual(current.status, AnalysisRunStatus.FAILED)
        self.assertEqual(events[-1].event_type, AnalysisEventType.RUN_FAILED)
        self.assertEqual(
            events[-1].payload["code"],
            ExecutionFailureCode.INPUT_UNAVAILABLE.value,
        )

    async def test_a_published_execution_is_recorded_on_the_run(self) -> None:
        """The read API resolves an execution from a run, so the run must
        point at it. Only a published execution earns a pointer (9.14.1)."""

        from scripts.data_analysis_agent.runtime.execution.publication import (
            ResultPublisher,
        )
        from scripts.data_analysis_agent.runtime.repositories.executions import (
            InMemoryExecutionRepository,
        )
        from tests.test_data_analysis_phase9_durability import (
            RecordingBlobStore,
        )

        harness = WorkerAdmissionLifecycleTests()
        repository = InMemoryExecutionRepository()
        service = NativeExecutionService(
            resolver=_ExecutionStubResolver(),
            publisher=ResultPublisher(
                repository=repository,
                store=RecordingBlobStore(),
            ),
            capabilities=ExecutorCapabilities(native_execution_ready=True),
        )

        current, _events = await harness._run_worker(
            ExecutionAdmission.QUEUE,
            "worker-native-linkage",
            execution_service=service,
        )

        self.assertEqual(current.status, AnalysisRunStatus.SUCCEEDED)
        self.assertIsNotNone(current.current_execution_id)
        self.assertIsNotNone(current.current_execution_key)
        # The pointer resolves to a real record belonging to this run.
        execution = await repository.get_by_id(
            user_id=current.user_id,
            execution_id=current.current_execution_id,
        )
        self.assertIsNotNone(execution)
        self.assertEqual(execution.run_id, current.run_id)

    async def test_an_unpublished_execution_leaves_no_pointer(self) -> None:
        """Without blob storage nothing is published, so nothing is promised."""

        harness = WorkerAdmissionLifecycleTests()
        service = NativeExecutionService(
            resolver=_ExecutionStubResolver(),
            capabilities=ExecutorCapabilities(native_execution_ready=True),
        )

        current, _events = await harness._run_worker(
            ExecutionAdmission.QUEUE,
            "worker-native-no-linkage",
            execution_service=service,
        )

        self.assertEqual(current.status, AnalysisRunStatus.SUCCEEDED)
        self.assertIsNone(current.current_execution_id)
        self.assertIsNone(current.current_execution_key)

    async def test_a_queued_run_without_an_engine_stays_queued(self) -> None:
        harness = WorkerAdmissionLifecycleTests()

        current, events = await harness._run_worker(
            ExecutionAdmission.QUEUE,
            "worker-native-absent",
            execution_service=None,
        )

        self.assertEqual(current.status, AnalysisRunStatus.WAITING)
        self.assertEqual(current.outcome, AnalysisRunOutcome.QUEUED_FOR_EXECUTION)
        self.assertEqual(events[-1].event_type, AnalysisEventType.EXECUTION_QUEUED)


class _ExecutionStubResolver:
    async def resolve(self, *, user_id, workspace_id, datasets):
        return tuple(
            ResolvedInput(
                alias=dataset.alias,
                dataset_id=dataset.dataset_id,
                content_signature=dataset_content_signature(dataset),
                columns=dataset.columns,
                rows=tuple(
                    {
                        column.key: (
                            100_000.0
                            if column.data_type.value == "currency"
                            else f"value-{index}"
                        )
                        for column in dataset.columns
                    }
                    for index in range(dataset.row_count)
                ),
            )
            for dataset in datasets
        )


class _ExecutionFailingResolver:
    async def resolve(self, *, user_id, workspace_id, datasets):
        raise InputResolutionError(
            ExecutionFailureCode.INPUT_UNAVAILABLE,
            "the normalized dataset is no longer available",
        )


class ApprovalAdmissionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def _approve(self, capabilities: ExecutorCapabilities):
        database = _Database()
        clock = _Clock()
        run_store = MongoAnalysisRunStore(database)
        state_machine = AnalysisRunStateMachine(run_store, clock=clock)
        repository = MongoAnalysisPlanRepository(database, capabilities=capabilities)
        context = _context(capabilities=capabilities)
        plan = _approval_plan(context)
        service = AnalysisPlanningService(
            repository=repository,
            state_machine=state_machine,
            context_builder=_ContextBuilder(context),
            planner=_Planner((_proposal(),)),
        )
        await state_machine.create_run(run=_run(context))
        await repository.create_plan(plan)
        await state_machine.transition(
            user_id=_USER_ID,
            run_id=context.run_id,
            target_status=AnalysisRunStatus.ACTIVE,
            target_phase=AnalysisRunPhase.PLANNING,
            outcome=None,
            event_type=AnalysisEventType.PLANNING_STARTED,
            deduplication_key="planning-started",
        )
        await state_machine.transition(
            user_id=_USER_ID,
            run_id=context.run_id,
            target_status=AnalysisRunStatus.WAITING,
            target_phase=AnalysisRunPhase.APPROVAL,
            outcome=AnalysisRunOutcome.PLAN_READY,
            event_type=AnalysisEventType.PLAN_APPROVAL_REQUIRED,
            deduplication_key="approval-required",
            summary_updates={
                "current_plan_id": plan.plan_id,
                "current_plan_revision": plan.revision,
                "current_plan_hash": plan.plan_hash,
                "plan_approval_status": RunApprovalStatus.PENDING,
            },
        )
        command = PlanApprovalCommand(
            decision="approve",
            plan_id=plan.plan_id,
            expected_revision=plan.revision,
            expected_plan_hash=plan.plan_hash,
            expected_input_signature=plan.input_signature,
            workbook_guards=context.workbook_guards,
            decision_id=str(uuid4()),
        )
        approved = await service.decide_plan(
            user_id=_USER_ID,
            run_id=context.run_id,
            command=command,
        )
        # A replay of the same decision must be idempotent in both deployments.
        await service.decide_plan(
            user_id=_USER_ID,
            run_id=context.run_id,
            command=command,
        )
        current = await state_machine.require_run(
            user_id=_USER_ID,
            run_id=context.run_id,
        )
        events = await run_store.list_events(
            user_id=_USER_ID,
            run_id=context.run_id,
            limit=100,
        )
        return approved, current, events

    async def test_approval_completes_the_run_without_an_engine(self) -> None:
        approved, run, events = await self._approve(ExecutorCapabilities())

        self.assertEqual(approved.approval.status, PlanApprovalStatus.APPROVED)
        self.assertEqual(run.status, AnalysisRunStatus.SUCCEEDED)
        self.assertEqual(run.phase, AnalysisRunPhase.COMPLETED)
        self.assertEqual(run.outcome, AnalysisRunOutcome.PLAN_READY)
        self.assertIsNotNone(run.completed_at)
        self.assertEqual(
            sum(
                event.event_type == AnalysisEventType.PLAN_APPROVED
                for event in events
            ),
            1,
        )

    async def test_approval_queues_the_run_with_an_engine(self) -> None:
        approved, run, events = await self._approve(NATIVE_ENGINE_READY)

        self.assertEqual(approved.approval.status, PlanApprovalStatus.APPROVED)
        self.assertEqual(run.status, AnalysisRunStatus.WAITING)
        self.assertEqual(run.phase, AnalysisRunPhase.EXECUTION)
        self.assertEqual(run.outcome, AnalysisRunOutcome.QUEUED_FOR_EXECUTION)
        self.assertIsNone(run.completed_at)
        self.assertEqual(
            sum(
                event.event_type == AnalysisEventType.PLAN_APPROVED
                for event in events
            ),
            1,
        )
        self.assertTrue(events[-1].payload["queued_for_execution"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
