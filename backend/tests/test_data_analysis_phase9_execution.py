"""Phase 9.3: durable input resolution, admission and execution identity.

The acceptance criteria these cover:

* restarting the backend after plan creation does not lose the inputs — inputs
  are resolved from persisted records, not from worker memory;
* a stale or missing input blocks execution before any work begins;
* duplicate queue delivery produces one logical execution;
* cache reuse is possible only for identical immutable inputs and versions.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.data_analysis_agent.analysis.models.preparation import (
    MaterializationType,
    NormalizedDatasetReference,
)
from scripts.data_analysis_agent.runtime.execution import (
    ExecutionAdmission,
    ExecutionFailureCode,
    ExecutionLimits,
    InputResolutionError,
    MongoNormalizedInputResolver,
    NativeExecutionService,
    ResolvedInput,
    RunAdmissionState,
    check_execution_preconditions,
    compile_recipe,
    evaluate_admission,
    execution_key,
)
from scripts.data_analysis_agent.runtime.execution.dag import RecipeCompilationError
from scripts.data_analysis_agent.runtime.execution.idempotency import (
    dataset_content_signature,
)
from scripts.data_analysis_agent.runtime.execution.native import subprocess_backend
from scripts.data_analysis_agent.runtime.execution.native.subprocess_backend import (
    SubprocessNativeBackend,
    scrubbed_environment,
)
from scripts.data_analysis_agent.runtime.models.capabilities import (
    ExecutorCapabilities,
)
from scripts.data_analysis_agent.runtime.models.plans import (
    ApprovalPolicy,
    PlanApprovalRecord,
    PlanApprovalStatus,
    PlanDiagnostics,
    build_analysis_plan,
)

from tests.test_data_analysis_phase8_planning import (
    _context,
    _proposal,
    _service_draft,
)


ENGINE_READY = ExecutorCapabilities(native_execution_ready=True)


def _plan(context=None, *, with_write: bool = False):
    context = context or _context()
    return build_analysis_plan(
        draft=_service_draft(context, _proposal(with_write=with_write)),
        user_id=context.user_id,
        workspace_id=context.workspace_id,
        revision=1,
        approval_policy=ApprovalPolicy(
            plan_approval_required=False,
            final_patch_approval_required=with_write,
            auto_execute_read_only=not with_write,
        ),
        diagnostics=PlanDiagnostics(generation_attempt=1, repair_count=0),
        model="test-planner",
    )


def _run_state(plan, **overrides):
    values = {
        "user_id": plan.user_id,
        "workspace_id": plan.workspace_id,
        "current_plan_id": plan.plan_id,
        "current_plan_hash": plan.plan_hash,
        "cancellation_requested": False,
    }
    values.update(overrides)
    return RunAdmissionState(**values)


class _StubResolver:
    """Stands in for the durable repository, recording how often it is asked."""

    def __init__(self, *, rows_per_dataset=None) -> None:
        self.calls = 0
        self._rows = rows_per_dataset

    async def resolve(self, *, user_id, workspace_id, datasets):
        self.calls += 1
        resolved = []
        for dataset in datasets:
            count = self._rows if self._rows is not None else dataset.row_count
            rows = tuple(
                {
                    column.key: (
                        100_000.0
                        if column.data_type.value == "currency"
                        else f"value-{index}"
                    )
                    for column in dataset.columns
                }
                for index in range(count)
            )
            resolved.append(
                ResolvedInput(
                    alias=dataset.alias,
                    dataset_id=dataset.dataset_id,
                    content_signature=dataset_content_signature(dataset),
                    columns=dataset.columns,
                    rows=rows,
                )
            )
        return tuple(resolved)


class _FailingResolver:
    def __init__(self, error: InputResolutionError) -> None:
        self.error = error
        self.calls = 0

    async def resolve(self, *, user_id, workspace_id, datasets):
        self.calls += 1
        raise self.error


class ExecutionKeyTests(unittest.TestCase):
    def test_the_same_plan_produces_the_same_key(self) -> None:
        plan = _plan()

        self.assertEqual(
            execution_key(plan, result_alias="filtered_revenue"),
            execution_key(plan, result_alias="filtered_revenue"),
        )

    def test_a_different_tenant_produces_a_different_key(self) -> None:
        plan = _plan()
        other = plan.model_copy(update={"workspace_id": "workspace-other"})

        self.assertNotEqual(
            execution_key(plan, result_alias="filtered_revenue"),
            execution_key(other, result_alias="filtered_revenue"),
        )

    def test_a_different_engine_version_produces_a_different_key(self) -> None:
        plan = _plan()

        self.assertNotEqual(
            execution_key(plan, result_alias="filtered_revenue"),
            execution_key(plan, result_alias="filtered_revenue", engine="polars-9.9"),
        )

    def test_the_signature_binds_source_identity_not_only_the_recipe(self) -> None:
        plan = _plan()
        dataset = plan.input_datasets[0]
        relabelled = dataset.model_copy(
            update={"title": "A different display title"}
        )
        different_source = dataset.model_copy(
            update={
                "provenance": (
                    dataset.provenance[0].model_copy(
                        update={"source_dataset_id": "some_other_source"}
                    ),
                )
            }
        )

        self.assertEqual(
            dataset_content_signature(dataset),
            dataset_content_signature(relabelled),
        )
        self.assertNotEqual(
            dataset_content_signature(dataset),
            dataset_content_signature(different_source),
        )


class RecipeCompilationTests(unittest.TestCase):
    def test_a_supported_plan_compiles_to_one_terminal_result(self) -> None:
        recipe = compile_recipe(_plan())

        self.assertEqual(recipe.result_alias, "filtered_revenue")
        self.assertEqual(len(recipe.steps), 1)

    def test_an_unsupported_operation_refuses_to_compile(self) -> None:
        plan = _plan()
        pivot_like = plan.steps[0].model_copy(update={"step_id": "second_filter"})
        widened = plan.model_copy(
            update={"steps": (plan.steps[0], pivot_like)},
        )

        # Two steps both writing the same alias give two terminals, which is
        # exactly the ambiguity the compiler must refuse.
        with self.assertRaises(RecipeCompilationError):
            compile_recipe(widened)


class ExecutionAdmissionTests(unittest.TestCase):
    def test_a_ready_engine_admits_a_read_only_plan(self) -> None:
        plan = _plan()

        decision = check_execution_preconditions(
            plan,
            _run_state(plan),
            capabilities=ENGINE_READY,
        )

        self.assertIs(decision.admission, ExecutionAdmission.QUEUE)

    def test_an_edit_plan_stops_at_plan_ready_without_the_patch_protocol(self) -> None:
        plan = _plan(with_write=True)

        decision = evaluate_admission(plan, ENGINE_READY)

        self.assertIs(decision.admission, ExecutionAdmission.PLAN_ONLY)
        self.assertEqual(decision.code, "workbook_patches_not_installed")

    def test_a_superseded_plan_is_rejected(self) -> None:
        plan = _plan()

        decision = check_execution_preconditions(
            plan,
            _run_state(plan, current_plan_hash="f" * 64),
            capabilities=ENGINE_READY,
        )

        self.assertTrue(decision.rejected)
        self.assertEqual(decision.code, "execution_plan_superseded")

    def test_a_cancelled_run_is_rejected(self) -> None:
        plan = _plan()

        decision = check_execution_preconditions(
            plan,
            _run_state(plan, cancellation_requested=True),
            capabilities=ENGINE_READY,
        )

        self.assertTrue(decision.rejected)
        self.assertEqual(decision.code, "execution_cancelled")

    def test_a_plan_awaiting_approval_cannot_execute(self) -> None:
        plan = _plan()
        pending = plan.model_copy(
            update={
                "approval": PlanApprovalRecord(
                    status=PlanApprovalStatus.PENDING,
                    requested_at=plan.created_at,
                )
            }
        )

        decision = check_execution_preconditions(
            pending,
            _run_state(pending),
            capabilities=ENGINE_READY,
        )

        self.assertTrue(decision.rejected)
        self.assertEqual(decision.code, "execution_awaiting_approval")

    def test_a_tenant_mismatch_is_rejected(self) -> None:
        plan = _plan()

        decision = check_execution_preconditions(
            plan,
            _run_state(plan, user_id="someone-else"),
            capabilities=ENGINE_READY,
        )

        self.assertTrue(decision.rejected)
        self.assertEqual(decision.code, "execution_tenant_mismatch")


class InputVerificationTests(unittest.IsolatedAsyncioTestCase):
    def _reference(self, dataset, **overrides):
        values = {
            "normalized_dataset_id": dataset.dataset_id,
            "cache_key": "b" * 64,
            "recipe_hash": dataset.dataset_version,
            "materialization": MaterializationType.MATERIALIZED_DATASET,
            "source_dataset_ids": tuple(
                item.source_dataset_id for item in dataset.provenance
            ),
            "source_versions": tuple(
                item.source_version for item in dataset.provenance
            ),
            "source_table_ids": tuple(
                f"table_{index}" for index, _ in enumerate(dataset.provenance)
            ),
            "source_type": "spreadsheet_range",
            "document_id": "artifact-1",
            "artifact_id": "artifact-1",
            "artifact_version_id": "artifact-version-1",
            "worksheet_id": "sheet-1",
            "range_a1": "Sheet1!A1:B101",
            "snapshot_hash": "c" * 64,
            "title": dataset.title,
            "columns": tuple(
                {
                    "key": column.key,
                    "label": column.label,
                    "data_type": (
                        "decimal"
                        if column.data_type.value in {"currency", "percentage"}
                        else column.data_type.value
                    ),
                    "unit": column.unit,
                }
                for column in dataset.columns
            ),
            "input_column_count": len(dataset.columns),
            "output_column_count": len(dataset.columns),
            "input_row_count": dataset.row_count,
            "retained_source_row_count": dataset.row_count,
            "output_row_count": dataset.row_count,
            "quality_score_before": 0.9,
            "quality_score_after": 0.95,
            "access": {
                "provider": "mongodb",
                "collection": "normalized_datasets",
                "record_id": dataset.dataset_id,
            },
        }
        values.update(overrides)
        return NormalizedDatasetReference.model_validate(values)

    def _database(self, documents):
        class _Cursor:
            def __init__(self, items):
                self._items = items

            async def to_list(self, length=None):
                return list(self._items)

        class _Collection:
            def __init__(self, items):
                self._items = items

            def find(self, query, projection=None):
                ids = set(query["normalized_dataset_id"]["$in"])
                return _Cursor(
                    [
                        item
                        for item in self._items
                        if item["user_id"] == query["user_id"]
                        and item["normalized_dataset_id"] in ids
                    ]
                )

        class _Database:
            def __init__(self, items):
                self._items = items

            def __getitem__(self, name):
                return _Collection(self._items if name == "normalized_datasets" else [])

        return _Database(documents)

    def _document(self, plan, reference, *, rows=None):
        dataset = plan.input_datasets[0]
        return {
            "user_id": plan.user_id,
            "normalized_dataset_id": dataset.dataset_id,
            "cache_key": reference.cache_key,
            "reference": reference.model_dump(mode="python"),
            "rows": rows
            if rows is not None
            else [
                {column.key: None for column in dataset.columns}
                for _ in range(dataset.row_count)
            ],
        }

    async def test_a_persisted_input_resolves_without_worker_memory(self) -> None:
        plan = _plan()
        dataset = plan.input_datasets[0]
        reference = self._reference(dataset)
        resolver = MongoNormalizedInputResolver(
            self._database([self._document(plan, reference)])
        )

        resolved = await resolver.resolve(
            user_id=plan.user_id,
            workspace_id=plan.workspace_id,
            datasets=plan.input_datasets,
        )

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].row_count, dataset.row_count)
        self.assertEqual(
            resolved[0].content_signature,
            dataset_content_signature(dataset),
        )

    async def test_a_missing_input_blocks_execution(self) -> None:
        plan = _plan()
        resolver = MongoNormalizedInputResolver(self._database([]))

        with self.assertRaises(InputResolutionError) as caught:
            await resolver.resolve(
                user_id=plan.user_id,
                workspace_id=plan.workspace_id,
                datasets=plan.input_datasets,
            )

        self.assertEqual(
            caught.exception.code,
            ExecutionFailureCode.INPUT_UNAVAILABLE,
        )

    async def test_a_changed_recipe_blocks_execution(self) -> None:
        plan = _plan()
        dataset = plan.input_datasets[0]
        reference = self._reference(dataset, recipe_hash="d" * 64)
        resolver = MongoNormalizedInputResolver(
            self._database([self._document(plan, reference)])
        )

        with self.assertRaises(InputResolutionError) as caught:
            await resolver.resolve(
                user_id=plan.user_id,
                workspace_id=plan.workspace_id,
                datasets=plan.input_datasets,
            )

        self.assertEqual(
            caught.exception.code,
            ExecutionFailureCode.INPUT_VERSION_MISMATCH,
        )

    async def test_a_changed_source_version_blocks_execution(self) -> None:
        plan = _plan()
        dataset = plan.input_datasets[0]
        reference = self._reference(dataset, source_versions=("e" * 64,))
        resolver = MongoNormalizedInputResolver(
            self._database([self._document(plan, reference)])
        )

        with self.assertRaises(InputResolutionError) as caught:
            await resolver.resolve(
                user_id=plan.user_id,
                workspace_id=plan.workspace_id,
                datasets=plan.input_datasets,
            )

        self.assertEqual(
            caught.exception.code,
            ExecutionFailureCode.INPUT_VERSION_MISMATCH,
        )

    async def test_a_changed_row_count_blocks_execution(self) -> None:
        plan = _plan()
        dataset = plan.input_datasets[0]
        reference = self._reference(dataset)
        document = self._document(
            plan,
            reference,
            rows=[{column.key: None for column in dataset.columns}],
        )
        resolver = MongoNormalizedInputResolver(self._database([document]))

        with self.assertRaises(InputResolutionError) as caught:
            await resolver.resolve(
                user_id=plan.user_id,
                workspace_id=plan.workspace_id,
                datasets=plan.input_datasets,
            )

        self.assertEqual(
            caught.exception.code,
            ExecutionFailureCode.INPUT_VERSION_MISMATCH,
        )


class ExecutionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_read_only_plan_executes_and_publishes_a_hash(self) -> None:
        plan = _plan()
        service = NativeExecutionService(
            resolver=_StubResolver(),
            capabilities=ENGINE_READY,
        )

        outcome = await service.execute(plan=plan, run=_run_state(plan))

        self.assertTrue(outcome.succeeded, outcome.failure_message)
        self.assertEqual(outcome.row_count, plan.input_datasets[0].row_count)
        self.assertIsNotNone(outcome.content_hash)
        self.assertFalse(outcome.cache_hit)

    async def test_duplicate_delivery_produces_one_logical_execution(self) -> None:
        plan = _plan()
        resolver = _StubResolver()
        service = NativeExecutionService(
            resolver=resolver,
            capabilities=ENGINE_READY,
        )

        first = await service.execute(plan=plan, run=_run_state(plan))
        second = await service.execute(plan=plan, run=_run_state(plan))

        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(first.content_hash, second.content_hash)
        # The inputs were resolved once; the replay was served from the key.
        self.assertEqual(resolver.calls, 1)

    async def test_a_stale_input_fails_before_any_work_begins(self) -> None:
        plan = _plan()
        resolver = _FailingResolver(
            InputResolutionError(
                ExecutionFailureCode.INPUT_VERSION_MISMATCH,
                "dataset changed since planning",
            )
        )
        service = NativeExecutionService(
            resolver=resolver,
            capabilities=ENGINE_READY,
        )

        outcome = await service.execute(plan=plan, run=_run_state(plan))

        self.assertFalse(outcome.succeeded)
        self.assertEqual(
            outcome.failure_code,
            ExecutionFailureCode.INPUT_VERSION_MISMATCH,
        )

    async def test_a_cancelled_run_never_resolves_inputs(self) -> None:
        plan = _plan()
        resolver = _StubResolver()
        service = NativeExecutionService(
            resolver=resolver,
            capabilities=ENGINE_READY,
        )

        outcome = await service.execute(
            plan=plan,
            run=_run_state(plan, cancellation_requested=True),
        )

        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.failure_code, ExecutionFailureCode.CANCELLED)
        self.assertEqual(resolver.calls, 0)

    async def test_an_absent_engine_never_reaches_the_resolver(self) -> None:
        plan = _plan()
        resolver = _StubResolver()
        service = NativeExecutionService(
            resolver=resolver,
            capabilities=ExecutorCapabilities(),
        )

        outcome = await service.execute(plan=plan, run=_run_state(plan))

        self.assertFalse(outcome.succeeded)
        self.assertEqual(resolver.calls, 0)
        self.assertEqual(
            outcome.admission.code,
            "native_execution_not_installed",
        )

    async def test_the_staging_directory_is_removed_after_a_run(self) -> None:
        plan = _plan()
        root = Path(self.enterContext(_temporary_directory()))
        service = NativeExecutionService(
            resolver=_StubResolver(),
            capabilities=ENGINE_READY,
            staging_root=root,
        )

        outcome = await service.execute(plan=plan, run=_run_state(plan))

        self.assertTrue(outcome.succeeded, outcome.failure_message)
        self.assertEqual(list(root.iterdir()), [])


class SubprocessIsolationTests(unittest.IsolatedAsyncioTestCase):
    def test_the_child_environment_carries_no_application_secrets(self) -> None:
        secrets = {
            "MONGODB_URI": "mongodb://user:password@host/db",
            "OPENAI_API_KEY": "sk-not-a-real-key",
            "CLOUDINARY_API_SECRET": "cloudinary-secret",
            "QDRANT_API_KEY": "qdrant-secret",
        }
        for name, value in secrets.items():
            self.enterContext(_environment_variable(name, value))

        environment = scrubbed_environment(project_root=Path.cwd())

        for name in secrets:
            self.assertNotIn(name, environment)
        self.assertIn("PATH", environment)

    async def test_a_wall_clock_timeout_kills_the_child(self) -> None:
        plan = _plan()
        service = NativeExecutionService(
            resolver=_StubResolver(rows_per_dataset=5),
            backend=SubprocessNativeBackend(project_root=Path.cwd()),
            capabilities=ENGINE_READY,
            # Shorter than a Python interpreter can even start, so the parent
            # must terminate the child rather than wait for it.
            limits=ExecutionLimits(wall_clock_seconds=0.05),
        )

        outcome = await service.execute(
            plan=plan.model_copy(
                update={
                    "input_datasets": (
                        plan.input_datasets[0].model_copy(update={"row_count": 5}),
                    )
                }
            ),
            run=_run_state(plan),
        )

        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.failure_code, ExecutionFailureCode.TIMEOUT)

    async def test_cancelling_mid_run_terminates_the_child(self) -> None:
        plan = _plan()
        service = NativeExecutionService(
            resolver=_StubResolver(rows_per_dataset=5),
            backend=SubprocessNativeBackend(project_root=Path.cwd()),
            capabilities=ENGINE_READY,
        )

        async def already_cancelled() -> bool:
            return True

        # Poll immediately rather than after the default half second, so the
        # test asserts the kill path instead of racing the child's startup.
        self.enterContext(
            patch.object(subprocess_backend, "CANCELLATION_POLL_SECONDS", 0.01)
        )

        outcome = await service.execute(
            plan=plan.model_copy(
                update={
                    "input_datasets": (
                        plan.input_datasets[0].model_copy(update={"row_count": 5}),
                    )
                }
            ),
            run=_run_state(plan),
            cancelled=already_cancelled,
        )

        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.failure_code, ExecutionFailureCode.CANCELLED)

    async def test_a_real_child_process_executes_the_recipe(self) -> None:
        plan = _plan()
        service = NativeExecutionService(
            resolver=_StubResolver(rows_per_dataset=5),
            backend=SubprocessNativeBackend(project_root=Path.cwd()),
            capabilities=ENGINE_READY,
        )

        outcome = await service.execute(
            plan=plan.model_copy(
                update={
                    "input_datasets": (
                        plan.input_datasets[0].model_copy(
                            update={"row_count": 5}
                        ),
                    )
                }
            ),
            run=_run_state(plan),
        )

        self.assertTrue(outcome.succeeded, outcome.failure_message)
        self.assertEqual(outcome.isolation, "bounded_subprocess")
        self.assertEqual(outcome.row_count, 5)


class _environment_variable:
    def __init__(self, name: str, value: str) -> None:
        self._name = name
        self._value = value
        self._previous: str | None = None

    def __enter__(self) -> None:
        self._previous = os.environ.get(self._name)
        os.environ[self._name] = self._value

    def __exit__(self, *_exc: object) -> None:
        if self._previous is None:
            os.environ.pop(self._name, None)
        else:
            os.environ[self._name] = self._previous


def _temporary_directory():
    import tempfile

    return tempfile.TemporaryDirectory()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
