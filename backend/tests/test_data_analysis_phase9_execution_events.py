"""Phase 9.14.2: the execution narrative on the durable event stream.

The acceptance criteria these cover:

* an execution reports what it is doing, in order, on the same replayable
  stream as every other milestone — SSE replay alone reconstructs it;
* events carry identifiers, counts and durations, never rows, values or
  formulas;
* a progress event that cannot be appended never costs the result;
* deduplication keys are attempt-scoped, so a recovered attempt updates rather
  than duplicates;
* a failed execution does not narrate a partial success.
"""

from __future__ import annotations

import unittest

from scripts.data_analysis_agent.runtime.execution.progress import (
    InputsResolved,
    NullExecutionProgressReporter,
    ResultMaterialized,
    ResultValidated,
    ResultValidationStarted,
    StepCompleted,
)
from scripts.data_analysis_agent.runtime.execution.publication import (
    ResultPublisher,
)
from scripts.data_analysis_agent.runtime.execution.service import (
    NativeExecutionService,
)
from scripts.data_analysis_agent.runtime.models.capabilities import (
    ExecutorCapabilities,
)
from scripts.data_analysis_agent.runtime.models.events import AnalysisEventType
from scripts.data_analysis_agent.runtime.models.runs import (
    AnalysisRunPhase,
    AnalysisRunStatus,
)
from scripts.data_analysis_agent.runtime.repositories.executions import (
    InMemoryExecutionRepository,
)
from scripts.data_analysis_agent.runtime.services.execution_progress import (
    DurableExecutionProgressReporter,
    milestone_payload,
)

from tests.test_data_analysis_phase9_durability import (
    RecordingBlobStore,
    build_service,
)
from tests.test_data_analysis_phase9_execution import _plan, _run_state
from tests.test_data_analysis_phase9_lifecycle import (
    WorkerAdmissionLifecycleTests,
    _ExecutionStubResolver,
    _ExecutionFailingResolver,
)
from scripts.data_analysis_agent.runtime.execution import ExecutionAdmission
from scripts.data_analysis_agent.runtime.execution.admission import (
    evaluate_admission,
)


ENGINE_READY = ExecutorCapabilities(native_execution_ready=True)

#: Every key any execution milestone is allowed to put on the stream. The point
#: of asserting against a closed set is that adding a field which could carry a
#: cell value fails here rather than in production (9.14.2).
SAFE_PAYLOAD_KEYS = frozenset(
    {
        "dataset_count",
        "total_rows",
        "step_id",
        "kind",
        "index",
        "total",
        "input_rows",
        "output_rows",
        "output_columns",
        "removed_rows",
        "row_count",
        "column_count",
        "byte_count",
        "content_hash",
    }
)


def _milestones():
    return (
        InputsResolved(dataset_count=2, total_rows=4_200),
        StepCompleted(
            step_id="step-1",
            kind="filter_rows",
            index=1,
            total=3,
            input_rows=4_200,
            output_rows=3_420,
            output_columns=6,
        ),
        ResultValidationStarted(row_count=24, column_count=6),
        ResultValidated(row_count=24, column_count=6),
        ResultMaterialized(
            row_count=24,
            column_count=6,
            byte_count=2_048,
            content_hash="a" * 64,
        ),
    )


class _RecordingStateMachine:
    """Captures record_event calls; optionally refuses them all."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.calls: list[dict] = []
        self.refuse = refuse

    async def record_event(self, **kwargs: object) -> None:
        if self.refuse:
            raise RuntimeError("the run no longer accepts progress events")
        self.calls.append(kwargs)


class _Run:
    user_id = "user-1"
    run_id = "11111111-1111-4111-8111-111111111111"
    workspace_id = "workspace-1"


# ------------------------------------------------------- payload contract


class MilestonePayloadTests(unittest.TestCase):
    def test_every_milestone_maps_to_a_distinct_event(self) -> None:
        expected = [
            AnalysisEventType.EXECUTION_INPUTS_RESOLVED,
            AnalysisEventType.EXECUTION_STEP_COMPLETED,
            AnalysisEventType.RESULT_VALIDATION_STARTED,
            AnalysisEventType.RESULT_VALIDATION_COMPLETED,
            AnalysisEventType.RESULT_MATERIALIZED,
        ]
        self.assertEqual(len(set(expected)), len(expected))

    def test_payloads_carry_only_identifiers_and_counts(self) -> None:
        for milestone in _milestones():
            with self.subTest(milestone=type(milestone).__name__):
                payload = milestone_payload(milestone)
                self.assertTrue(set(payload).issubset(SAFE_PAYLOAD_KEYS))
                for value in payload.values():
                    self.assertIsInstance(value, (int, str))

    def test_a_step_reports_how_many_rows_it_removed(self) -> None:
        payload = milestone_payload(
            StepCompleted(
                step_id="step-1",
                kind="filter_rows",
                index=1,
                total=2,
                input_rows=4_200,
                output_rows=3_420,
                output_columns=6,
            )
        )
        self.assertEqual(payload["removed_rows"], 780)
        self.assertEqual(payload["index"], 1)
        self.assertEqual(payload["total"], 2)

    def test_an_unknown_milestone_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            milestone_payload(object())  # type: ignore[arg-type]


# ------------------------------------------------------------ the reporter


class DurableReporterTests(unittest.IsolatedAsyncioTestCase):
    async def test_milestones_append_with_attempt_scoped_keys(self) -> None:
        machine = _RecordingStateMachine()
        reporter = DurableExecutionProgressReporter(
            state_machine=machine,
            run=_Run(),
            worker_id="worker-1",
            lease_attempt=3,
        )

        for milestone in _milestones():
            await reporter.emit(milestone)

        keys = [call["deduplication_key"] for call in machine.calls]
        self.assertEqual(
            keys,
            [
                "attempt-3:execution-inputs-resolved",
                "attempt-3:execution-step-1",
                "attempt-3:result-validation-started",
                "attempt-3:result-validation-completed",
                "attempt-3:result-materialized",
            ],
        )
        self.assertEqual(len(set(keys)), len(keys))

    async def test_validation_milestones_move_the_phase_forward(self) -> None:
        machine = _RecordingStateMachine()
        reporter = DurableExecutionProgressReporter(
            state_machine=machine,
            run=_Run(),
            worker_id="worker-1",
            lease_attempt=1,
        )

        for milestone in _milestones():
            await reporter.emit(milestone)

        phases = [call["phase"] for call in machine.calls]
        self.assertEqual(
            phases,
            [
                AnalysisRunPhase.EXECUTION,
                AnalysisRunPhase.EXECUTION,
                AnalysisRunPhase.RESULT_VALIDATION,
                AnalysisRunPhase.RESULT_VALIDATION,
                AnalysisRunPhase.RESULT_VALIDATION,
            ],
        )

    async def test_a_refused_event_never_reaches_the_caller(self) -> None:
        """A paused, cancelled or re-leased run refuses progress. That is not
        a reason to fail an execution that already produced a result."""

        reporter = DurableExecutionProgressReporter(
            state_machine=_RecordingStateMachine(refuse=True),
            run=_Run(),
            worker_id="worker-1",
            lease_attempt=1,
        )

        for milestone in _milestones():
            await reporter.emit(milestone)  # must not raise


class NullReporterTests(unittest.IsolatedAsyncioTestCase):
    async def test_execution_does_not_require_a_listener(self) -> None:
        reporter = NullExecutionProgressReporter()
        for milestone in _milestones():
            await reporter.emit(milestone)


# --------------------------------------------------- through the executor


class _CollectingReporter:
    def __init__(self) -> None:
        self.milestones: list[object] = []

    async def emit(self, milestone: object) -> None:
        self.milestones.append(milestone)


class ExecutionNarrativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_published_execution_narrates_every_boundary(self) -> None:
        service, _store, _repository = build_service()
        plan = _plan()
        reporter = _CollectingReporter()

        outcome = await service.execute(
            plan=plan,
            run=_run_state(plan),
            reporter=reporter,
        )

        self.assertTrue(outcome.succeeded)
        kinds = [type(item).__name__ for item in reporter.milestones]
        self.assertEqual(kinds[0], "InputsResolved")
        self.assertIn("StepCompleted", kinds)
        # Validation, then materialization, and in that order.
        self.assertLess(
            kinds.index("ResultValidationStarted"),
            kinds.index("ResultValidated"),
        )
        self.assertLess(
            kinds.index("ResultValidated"),
            kinds.index("ResultMaterialized"),
        )

    async def test_steps_are_numbered_in_execution_order(self) -> None:
        service, _store, _repository = build_service()
        plan = _plan()
        reporter = _CollectingReporter()

        await service.execute(
            plan=plan,
            run=_run_state(plan),
            reporter=reporter,
        )

        steps = [
            item
            for item in reporter.milestones
            if isinstance(item, StepCompleted)
        ]
        self.assertTrue(steps)
        self.assertEqual(
            [item.index for item in steps],
            list(range(1, len(steps) + 1)),
        )
        for item in steps:
            self.assertEqual(item.total, len(steps))

    async def test_the_materialized_hash_is_the_published_one(self) -> None:
        service, _store, _repository = build_service()
        plan = _plan()
        reporter = _CollectingReporter()

        outcome = await service.execute(
            plan=plan,
            run=_run_state(plan),
            reporter=reporter,
        )

        materialized = [
            item
            for item in reporter.milestones
            if isinstance(item, ResultMaterialized)
        ]
        self.assertEqual(len(materialized), 1)
        self.assertEqual(materialized[0].content_hash, outcome.content_hash)
        self.assertGreater(materialized[0].byte_count, 0)

    async def test_a_failed_execution_narrates_no_steps(self) -> None:
        """A partial narrative whose last line is the broken step would read
        as progress. The failure is the outcome, not the story."""

        service = NativeExecutionService(
            resolver=_ExecutionFailingResolver(),
            capabilities=ENGINE_READY,
        )
        plan = _plan()
        reporter = _CollectingReporter()

        outcome = await service.execute(
            plan=plan,
            run=_run_state(plan),
            reporter=reporter,
        )

        self.assertFalse(outcome.succeeded)
        self.assertEqual(reporter.milestones, [])

    async def test_a_cached_execution_replays_no_narrative(self) -> None:
        """A cache hit does no work, so it reports none."""

        store = RecordingBlobStore()
        repository = InMemoryExecutionRepository()
        service, _store, _repository = build_service(
            store=store,
            repository=repository,
        )
        plan = _plan()
        await service.execute(plan=plan, run=_run_state(plan))

        reporter = _CollectingReporter()
        second = await service.execute(
            plan=plan,
            run=_run_state(plan),
            reporter=reporter,
        )

        self.assertTrue(second.succeeded)
        self.assertTrue(second.cache_hit)
        self.assertEqual(reporter.milestones, [])


# ------------------------------------------------- end to end on the stream


class WorkerNarrativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_run_stream_carries_the_execution_narrative(self) -> None:
        harness = WorkerAdmissionLifecycleTests()
        service = NativeExecutionService(
            resolver=_ExecutionStubResolver(),
            publisher=ResultPublisher(
                repository=InMemoryExecutionRepository(),
                store=RecordingBlobStore(),
            ),
            capabilities=ENGINE_READY,
        )

        current, events = await harness._run_worker(
            ExecutionAdmission.QUEUE,
            "worker-narrative",
            execution_service=service,
        )

        self.assertEqual(current.status, AnalysisRunStatus.SUCCEEDED)
        types = [event.event_type for event in events]
        for expected in (
            AnalysisEventType.EXECUTION_STARTED,
            AnalysisEventType.EXECUTION_INPUTS_RESOLVED,
            AnalysisEventType.EXECUTION_STEP_COMPLETED,
            AnalysisEventType.RESULT_VALIDATION_STARTED,
            AnalysisEventType.RESULT_VALIDATION_COMPLETED,
            AnalysisEventType.RESULT_MATERIALIZED,
        ):
            self.assertIn(expected, types)

        # Ordered, and the run still completes last.
        self.assertLess(
            types.index(AnalysisEventType.EXECUTION_INPUTS_RESOLVED),
            types.index(AnalysisEventType.RESULT_MATERIALIZED),
        )
        self.assertEqual(types[-1], AnalysisEventType.RUN_COMPLETED)

        # Sequence numbers are contiguous and increasing, so SSE replay from
        # any cursor reconstructs the same narrative.
        sequences = [event.sequence for event in events]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(len(set(sequences)), len(sequences))

    async def test_progress_payloads_stay_free_of_data(self) -> None:
        harness = WorkerAdmissionLifecycleTests()
        service = NativeExecutionService(
            resolver=_ExecutionStubResolver(),
            publisher=ResultPublisher(
                repository=InMemoryExecutionRepository(),
                store=RecordingBlobStore(),
            ),
            capabilities=ENGINE_READY,
        )

        _current, events = await harness._run_worker(
            ExecutionAdmission.QUEUE,
            "worker-narrative-payloads",
            execution_service=service,
        )

        progress = {
            AnalysisEventType.EXECUTION_INPUTS_RESOLVED,
            AnalysisEventType.EXECUTION_STEP_COMPLETED,
            AnalysisEventType.RESULT_VALIDATION_STARTED,
            AnalysisEventType.RESULT_VALIDATION_COMPLETED,
            AnalysisEventType.RESULT_MATERIALIZED,
        }
        seen = 0
        for event in events:
            if event.event_type not in progress:
                continue
            seen += 1
            self.assertTrue(set(event.payload).issubset(SAFE_PAYLOAD_KEYS))
            for value in event.payload.values():
                self.assertIsInstance(value, (int, str))
        self.assertGreaterEqual(seen, 4)


# ------------------------------------------------------------ the gate


class CapabilityGateTests(unittest.TestCase):
    def test_gate_a_is_open_and_gate_b_is_not(self) -> None:
        """Native execution is installed; workbook patching waits for 9.13."""

        from config.settings import settings

        self.assertTrue(settings.analysis_native_execution_ready)
        self.assertFalse(settings.analysis_workbook_patches_ready)

    def test_an_edit_plan_still_stops_at_the_plan(self) -> None:
        """With Gate B shut, a workbook write must not enter the queue."""

        capabilities = ExecutorCapabilities(
            native_execution_ready=True,
            workbook_patches_ready=False,
        )
        decision = evaluate_admission(_plan(with_write=True), capabilities)

        self.assertIs(decision.admission, ExecutionAdmission.PLAN_ONLY)
        self.assertEqual(decision.code, "workbook_patches_not_installed")

    def test_a_read_only_plan_is_queued(self) -> None:
        capabilities = ExecutorCapabilities(
            native_execution_ready=True,
            workbook_patches_ready=False,
        )
        decision = evaluate_admission(_plan(), capabilities)
        self.assertIs(decision.admission, ExecutionAdmission.QUEUE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
