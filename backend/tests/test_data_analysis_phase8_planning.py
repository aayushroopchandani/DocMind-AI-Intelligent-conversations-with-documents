from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from scripts.data_analysis_agent.analysis.models.requirements import (
    AnalysisOperation,
    AnalysisRequirements,
    ExpectedDataType,
    FilterOperator,
    RequirementItem,
    RequirementKind,
)
from scripts.data_analysis_agent.runtime.models.datasets import DatasetSourceType
from scripts.data_analysis_agent.runtime.models.events import AnalysisEventType
from scripts.data_analysis_agent.runtime.models.expressions import (
    ColumnExpression,
    CompareExpression,
    LiteralExpression,
    SetExpression,
)
from scripts.data_analysis_agent.runtime.models.plans import (
    AnalysisPlan,
    AnalysisPlanDraft,
    ApprovalPolicy,
    ApprovalReason,
    ComposeResponseStep,
    DeriveColumnStep,
    FilterRowsStep,
    FinalPatchApprovalCommand,
    FinalPatchProposal,
    PatchImpactSummary,
    PlanApprovalCommand,
    PlanApprovalRecord,
    PlanApprovalStatus,
    PlanColumn,
    PlanDataType,
    PlanDatasetProvenance,
    PlanDiagnostics,
    PlanExecutor,
    PlanInputDataset,
    PlanProposal,
    PlanStepEstimate,
    StepProvenance,
    TrainModelStep,
    VisualizationStep,
    WorkbookCollisionPolicy,
    WorkbookPlacementPolicy,
    WorkbookVersionGuard,
    WorkbookWriteIntent,
    WorkbookWriteTarget,
    build_analysis_plan,
    compute_input_signature,
)
from scripts.data_analysis_agent.runtime.models.runs import (
    AnalysisMode,
    AnalysisRun,
    AnalysisRunOutcome,
    AnalysisRunPhase,
    AnalysisRunStatus,
    RunApprovalStatus,
)
from scripts.data_analysis_agent.runtime.integration.contracts import (
    Phase7PlanningArtifacts,
)
from scripts.data_analysis_agent.runtime.planning.context import (
    PlanningColumnSummary,
    PlanningContext,
    PlanningDatasetSummary,
)
from scripts.data_analysis_agent.runtime.execution.admission import (
    ExecutionAdmission,
)
from scripts.data_analysis_agent.runtime.planning.contracts import (
    ExecutorCapabilities,
    PlanResourcePolicy,
    PlanningOutcome,
    PlanValidationReport,
    PlanningExecutionResult,
    PlanningProgress,
)
from scripts.data_analysis_agent.runtime.planning.planner import (
    PlannerInvocation,
    PlannerOutputError,
    TypedAnalysisPlanner,
)
from scripts.data_analysis_agent.runtime.planning.service import (
    AnalysisPlanningService,
    _draft as _service_draft,
)
from scripts.data_analysis_agent.runtime.planning.validation import (
    AnalysisPlanValidator,
    derive_approval_policy,
)
from scripts.data_analysis_agent.runtime.repositories.plans import (
    AnalysisPlanConflictError,
    AnalysisPlanRepositoryError,
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
from tests.test_data_analysis_phase7_runtime_adapter import _dataset_handle
from tests.test_data_analysis_runtime_runs import _Clock, _Database
from tests.test_data_analysis_runtime_worker import (
    _DatasetCatalog,
    _ResultAdapter,
    _dataset_run,
    _prepared_result,
)


_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64
_USER_ID = "user-1"
_WORKSPACE_ID = "workspace-1"


# Real normalized IDs match `^normalized_[a-f0-9]{24}$`. Using a realistic one
# here lets execution tests resolve this dataset against the durable
# normalized-dataset contract instead of a shape that could never be stored.
NORMALIZED_DATASET_ID = "normalized_0123456789abcdef01234567"


def _input_dataset() -> PlanInputDataset:
    return PlanInputDataset(
        alias="input_1",
        dataset_id=NORMALIZED_DATASET_ID,
        dataset_version=_HASH_C,
        title="Revenue data",
        row_count=100,
        columns=(
            PlanColumn(
                key="company",
                label="Company",
                data_type=PlanDataType.STRING,
            ),
            PlanColumn(
                key="revenue",
                label="Revenue",
                data_type=PlanDataType.CURRENCY,
                unit="USD",
            ),
        ),
        provenance=(
            PlanDatasetProvenance(
                source_dataset_id="source_dataset_1",
                source_version=_HASH_A,
                source_type=DatasetSourceType.SPREADSHEET_RANGE,
                artifact_id="artifact-1",
                artifact_version_id="artifact-version-1",
                workbook_id="workbook-1",
                workbook_revision=12,
                worksheet_id="sheet-1",
                range_a1="Sheet1!A1:B101",
                snapshot_hash=_HASH_B,
            ),
        ),
    )


def _requirements() -> AnalysisRequirements:
    return AnalysisRequirements(
        model="test-requirements-model",
        operation=AnalysisOperation.LOOKUP,
        selected_document_ids=("artifact-1",),
        requirements=(
            RequirementItem(
                requirement_id="req_revenue_filter",
                kind=RequirementKind.FILTER,
                name="revenue greater than 50000",
                expected_data_type=ExpectedDataType.NUMBER,
                unit="USD",
                filter_operator=FilterOperator.GREATER_THAN,
                filter_values=("50000",),
            ),
        ),
        table_evidence_required=True,
    )


# A deployment where Phase 9.4's native engine is installed. Approval and
# planning queue work here instead of completing the run at plan_ready.
NATIVE_ENGINE_READY = ExecutorCapabilities(native_execution_ready=True)


def _context(
    *,
    run_id: str | None = None,
    mode: AnalysisMode = AnalysisMode.EDIT,
    capabilities: ExecutorCapabilities | None = None,
) -> PlanningContext:
    dataset = _input_dataset()
    return PlanningContext(
        run_id=run_id or str(uuid4()),
        user_id=_USER_ID,
        workspace_id=_WORKSPACE_ID,
        mode=mode,
        prompt=(
            "Filter rows where revenue is greater than 50,000 and put the "
            "result next to the current table."
        ),
        input_signature=compute_input_signature((dataset,)),
        requirements=_requirements(),
        input_datasets=(dataset,),
        dataset_summaries=(
            PlanningDatasetSummary(
                alias=dataset.alias,
                dataset_id=dataset.dataset_id,
                title=dataset.title,
                row_count=dataset.row_count,
                columns=(
                    PlanningColumnSummary(
                        key="company",
                        label="Company",
                        data_type=PlanDataType.STRING,
                        semantic_role="dimension",
                        missing_percentage=0,
                        unique_count=100,
                        example_values=("Example Co",),
                    ),
                    PlanningColumnSummary(
                        key="revenue",
                        label="Revenue",
                        data_type=PlanDataType.CURRENCY,
                        unit="USD",
                        semantic_role="metric",
                        missing_percentage=0,
                        unique_count=90,
                        example_values=("75000",),
                        minimum=1000,
                        maximum=500000,
                    ),
                ),
            ),
        ),
        workbook_guards=(
            WorkbookVersionGuard(
                workbook_id="workbook-1",
                worksheet_id="sheet-1",
                workbook_revision=12,
                snapshot_hash=_HASH_B,
            ),
        ),
        capabilities=capabilities or ExecutorCapabilities(),
        resource_policy=PlanResourcePolicy(),
    )


def _proposal(
    *,
    column_key: str = "revenue",
    executor: PlanExecutor = PlanExecutor.NATIVE,
    destructive: bool = False,
    overwrite_formulas: bool = False,
    with_write: bool = True,
) -> PlanProposal:
    dataset = _input_dataset()
    step = FilterRowsStep(
        step_id="filter_revenue",
        executor=executor,
        python_reason=(
            "Use Python for this requested operation."
            if executor == PlanExecutor.PYTHON
            else None
        ),
        input_alias="input_1",
        output_alias="filtered_revenue",
        predicate=CompareExpression(
            operator="greater_than",
            left=ColumnExpression(column_key=column_key),
            right=LiteralExpression(
                value=50_000,
                data_type="currency",
                unit="USD",
            ),
        ),
        expected_schema=dataset.columns,
        estimate=PlanStepEstimate(
            rows_scanned=100,
            duration_seconds=1,
        ),
        provenance=StepProvenance(
            source_dataset_ids=("source_dataset_1",),
            source_versions=(_HASH_A,),
            description="Rows retain exact spreadsheet source lineage.",
        ),
    )
    write_intents = (
        WorkbookWriteIntent(
            intent_id="write_filtered",
            input_alias=step.output_alias,
            target=WorkbookWriteTarget(
                workbook_id="workbook-1",
                worksheet_id="sheet-1",
                base_workbook_revision=12,
                base_snapshot_hash=_HASH_B,
                source_range_a1="Sheet1!A1:B101",
                placement_policy=WorkbookPlacementPolicy.ADJACENT_RIGHT,
                collision_policy=WorkbookCollisionPolicy.REQUIRE_REAPPROVAL,
            ),
            destructive=destructive,
            overwrite_formulas=overwrite_formulas,
        ),
    ) if with_write else ()
    return PlanProposal(
        intent="Filter the current revenue table without changing its source.",
        steps=(step,),
        write_intents=write_intents,
    )


def _draft(context: PlanningContext, proposal: PlanProposal) -> AnalysisPlanDraft:
    return AnalysisPlanDraft(
        **proposal.model_dump(mode="python"),
        run_id=context.run_id,
        mode=context.mode,
        input_signature=context.input_signature,
        input_datasets=context.input_datasets,
    )


def _run(context: PlanningContext) -> AnalysisRun:
    return AnalysisRun(
        run_id=context.run_id,
        user_id=context.user_id,
        workspace_id=context.workspace_id,
        chat_id="chat-1",
        idempotency_key=f"idempotency-{context.run_id}",
        request_fingerprint=_HASH_A,
        mode=context.mode,
        prompt=context.prompt,
    )


def _approval_plan(context: PlanningContext):
    proposal = _proposal(destructive=True)
    draft = _draft(context, proposal)
    policy = derive_approval_policy(draft=draft, context=context)
    return build_analysis_plan(
        draft=draft,
        user_id=context.user_id,
        workspace_id=context.workspace_id,
        revision=1,
        approval_policy=policy,
        diagnostics=PlanDiagnostics(generation_attempt=1, repair_count=0),
        model="test-planner",
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class _ContextBuilder:
    def __init__(self, context: PlanningContext) -> None:
        self.context = context

    def build(self, **_kwargs: object) -> PlanningContext:
        return self.context


class _Planner:
    def __init__(self, proposals: tuple[PlanProposal, ...]) -> None:
        self.proposals = proposals
        self.propose_calls = 0
        self.repair_calls = 0

    async def propose(self, _context: PlanningContext) -> PlannerInvocation:
        proposal = self.proposals[self.propose_calls]
        self.propose_calls += 1
        return PlannerInvocation(proposal=proposal, model="test-planner")

    async def repair(
        self,
        _context: PlanningContext,
        *,
        original: PlanProposal | None,
        issues: tuple[object, ...],
    ) -> PlannerInvocation:
        self.assert_repair_payload(original, issues)
        proposal = self.proposals[1]
        self.repair_calls += 1
        return PlannerInvocation(proposal=proposal, model="test-planner")

    @staticmethod
    def assert_repair_payload(
        original: PlanProposal | None,
        issues: tuple[object, ...],
    ) -> None:
        if original is None or not issues:
            raise AssertionError("repair must receive the original and safe issues")


class _Generator:
    def __init__(self, response: object) -> None:
        self.response = response

    async def ainvoke(self, _input: object, **_kwargs: object) -> object:
        return self.response


class _FailingGenerator:
    async def ainvoke(self, _input: object, **_kwargs: object) -> object:
        raise TimeoutError("provider request timed out")


class _PlanningRepository:
    def __init__(self) -> None:
        self.plan = None

    async def get_current_plan(self, **_kwargs: object):
        return self.plan

    async def list_reserved_write_targets(self, **_kwargs: object):
        return frozenset()

    async def create_plan(self, plan):
        self.plan = plan
        return plan


class _WorkerPlanningService:
    def __init__(self, result: PlanningExecutionResult) -> None:
        self.result = result
        self.calls = 0

    async def create_plan(self, *, reporter: object, **_kwargs: object):
        self.calls += 1
        await reporter.emit(
            PlanningProgress(
                event_type=AnalysisEventType.PLANNING_STARTED,
                phase=AnalysisRunPhase.PLANNING,
                payload={"mode": "edit", "input_dataset_count": 1},
                deduplication_key="planning:started",
            )
        )
        await reporter.emit(
            PlanningProgress(
                event_type=AnalysisEventType.PLAN_VALIDATION_STARTED,
                phase=AnalysisRunPhase.PLAN_VALIDATION,
                payload={"attempt": 1},
                deduplication_key="planning:validation:1",
            )
        )
        return self.result


class Phase8TypedPlanTests(unittest.TestCase):
    def test_normalized_internal_columns_are_valid_plan_symbols(self) -> None:
        column = PlanColumn(
            key="__series",
            label="Series",
            data_type=PlanDataType.STRING,
        )
        self.assertEqual(column.key, "__series")

    def test_malformed_write_ranges_fail_at_the_typed_boundary(self) -> None:
        with self.assertRaises(ValueError):
            WorkbookWriteTarget(
                workbook_id="workbook-1",
                worksheet_id="sheet-1",
                base_workbook_revision=12,
                base_snapshot_hash=_HASH_A,
                source_range_a1="Sheet1!A1:E8",
                placement_policy=WorkbookPlacementPolicy.EXACT_RANGE,
                exact_target_range_a1="the current table",
            )

    def test_visualization_artifacts_do_not_require_tabular_output_schema(self) -> None:
        step = VisualizationStep(
            step_id="plot_revenue",
            executor=PlanExecutor.PYTHON,
            output_alias="revenue_chart",
            expected_schema=(),
            input_alias="input_1",
            chart_type="scatter",
            x_column="company",
            y_columns=("revenue",),
            title="Revenue by company",
            python_reason="Generate a fitted analytical visualization.",
        )

        self.assertEqual(step.expected_schema, ())

    def test_model_artifacts_do_not_require_tabular_output_schema(self) -> None:
        step = TrainModelStep(
            step_id="train_knn",
            executor=PlanExecutor.PYTHON,
            output_alias="knn_model",
            expected_schema=(),
            input_alias="input_1",
            model_type="knn",
            feature_columns=("revenue",),
            target_column="company",
            python_reason="Train and evaluate the requested KNN model.",
        )

        self.assertEqual(step.expected_schema, ())

    def test_constant_derived_columns_do_not_require_source_column_reads(self) -> None:
        source = _input_dataset()
        output_column = PlanColumn(
            key="source_report",
            label="Source report",
            data_type=PlanDataType.STRING,
            nullable=False,
        )
        step = DeriveColumnStep(
            step_id="add_source_report",
            executor=PlanExecutor.NATIVE,
            output_alias="labeled_rows",
            input_alias="input_1",
            output_column=output_column,
            expression=LiteralExpression(
                value="Amazon report",
                data_type="string",
            ),
            expected_schema=(*source.columns, output_column),
        )

        self.assertEqual(step.referenced_columns, ())

    def test_server_canonicalizes_unambiguous_workbook_identity(self) -> None:
        context = _context()
        proposal = _proposal()
        intent = proposal.write_intents[0]
        assert isinstance(intent, WorkbookWriteIntent)
        untrusted = proposal.model_copy(
            update={
                "write_intents": (
                    intent.model_copy(
                        update={
                            "target": intent.target.model_copy(
                                update={
                                    "workbook_id": "invented-workbook",
                                    "worksheet_id": "invented-sheet",
                                    "base_workbook_revision": 999,
                                    "base_snapshot_hash": _HASH_A,
                                    "source_range_a1": "Other!C3:D4",
                                }
                            )
                        }
                    ),
                )
            }
        )

        draft = _service_draft(context, untrusted)
        target = draft.write_intents[0].target

        self.assertEqual(target.workbook_id, "workbook-1")
        self.assertEqual(target.worksheet_id, "sheet-1")
        self.assertEqual(target.base_workbook_revision, 12)
        self.assertEqual(target.base_snapshot_hash, _HASH_B)
        self.assertEqual(target.source_range_a1, "Sheet1!A1:B101")

    def test_server_connects_schema_only_response_to_prepared_inputs(self) -> None:
        context = _context(mode=AnalysisMode.ANALYSE)
        proposal = PlanProposal(
            intent="Describe the prepared dataset schema.",
            steps=(
                ComposeResponseStep(
                    step_id="describe_schema",
                    executor=PlanExecutor.NATIVE,
                    output_alias="schema_response",
                    input_aliases=(),
                    response_format="structured",
                ),
            ),
        )

        draft = _service_draft(context, proposal)
        step = draft.steps[0]

        assert isinstance(step, ComposeResponseStep)
        self.assertEqual(step.input_aliases, ("input_1",))

    def test_server_canonicalizes_mechanical_dag_schema_and_estimates(self) -> None:
        context = _context()
        proposal = _proposal(with_write=False)
        step = proposal.steps[0].model_copy(
            update={
                "depends_on": ("invented_step",),
                "expected_schema": (
                    PlanColumn(
                        key="invented",
                        label="Invented",
                        data_type=PlanDataType.STRING,
                    ),
                ),
                "estimate": PlanStepEstimate(rows_scanned=0),
            }
        )

        draft = _service_draft(
            context,
            proposal.model_copy(update={"steps": (step,)}),
        )
        canonical = draft.steps[0]

        self.assertEqual(canonical.depends_on, ())
        self.assertEqual(canonical.expected_schema, context.input_datasets[0].columns)
        self.assertEqual(canonical.estimate.rows_scanned, 100)

    def test_confusion_matrix_is_a_supported_python_visualization(self) -> None:
        context = _context(mode=AnalysisMode.ANALYSE)
        proposal = PlanProposal(
            intent="Create a confusion matrix for model evaluation.",
            steps=(
                VisualizationStep(
                    step_id="plot_confusion_matrix",
                    executor=PlanExecutor.PYTHON,
                    output_alias="confusion_matrix_chart",
                    input_alias="input_1",
                    chart_type="confusion_matrix",
                    title="KNN confusion matrix",
                    python_reason="Render classification evaluation results.",
                ),
            ),
        )

        report = AnalysisPlanValidator().validate(
            draft=_service_draft(context, proposal),
            context=context,
        )

        self.assertNotIn(
            "simple_chart_cannot_use_python",
            {issue.code for issue in report.errors},
        )

    def test_period_filter_type_is_derived_from_the_source_schema(self) -> None:
        base = _context(mode=AnalysisMode.ANALYSE)
        period = PlanColumn(
            key="__period",
            label="Period",
            data_type=PlanDataType.PERIOD,
        )
        dataset = base.input_datasets[0].model_copy(
            update={"columns": (*base.input_datasets[0].columns, period)}
        )
        context = base.model_copy(
            update={
                "input_datasets": (dataset,),
                "input_signature": compute_input_signature((dataset,)),
            }
        )
        proposal = PlanProposal(
            intent="Keep 2024 and 2023.",
            steps=(
                FilterRowsStep(
                    step_id="filter_periods",
                    executor=PlanExecutor.NATIVE,
                    output_alias="selected_periods",
                    input_alias="input_1",
                    predicate=SetExpression(
                            expression=ColumnExpression(column_key="__period"),
                            operator="in",
                            values=(
                                LiteralExpression(value="2024", data_type="period"),
                                LiteralExpression(value="2023", data_type="period"),
                            ),
                    ),
                    expected_schema=dataset.columns,
                ),
            ),
        )

        draft = _service_draft(context, proposal)
        predicate = draft.steps[0].predicate
        report = AnalysisPlanValidator().validate(draft=draft, context=context)

        self.assertIsInstance(predicate, SetExpression)
        self.assertEqual(predicate.values[0].data_type.value, "period")
        codes = {issue.code for issue in report.errors}
        self.assertNotIn("predicate_type_mismatch", codes)
        self.assertNotIn("predicate_literal_type_mismatch", codes)

    def test_column_labels_are_resolved_to_stable_source_keys(self) -> None:
        context = _context(mode=AnalysisMode.ANALYSE)
        proposal = _proposal(column_key="Revenue", with_write=False)

        draft = _service_draft(context, proposal)

        self.assertEqual(
            draft.steps[0].predicate.left.column_key,
            "revenue",
        )

    def test_valid_native_filter_and_two_level_approval_policy(self) -> None:
        context = _context()
        draft = _draft(context, _proposal())

        report = AnalysisPlanValidator().validate(
            draft=draft,
            context=context,
        )
        policy = derive_approval_policy(draft=draft, context=context)

        self.assertTrue(report.valid)
        self.assertFalse(policy.plan_approval_required)
        self.assertTrue(policy.final_patch_approval_required)
        self.assertFalse(policy.auto_execute_read_only)

        destructive = _draft(context, _proposal(destructive=True))
        destructive_policy = derive_approval_policy(
            draft=destructive,
            context=context,
        )
        self.assertTrue(destructive_policy.plan_approval_required)
        self.assertIn(
            ApprovalReason.DESTRUCTIVE_WRITE,
            destructive_policy.plan_approval_reasons,
        )

    def test_validator_rejects_missing_columns_and_python_for_native_work(self) -> None:
        context = _context()
        draft = _draft(
            context,
            _proposal(column_key="invented_revenue", executor=PlanExecutor.PYTHON),
        )

        report = AnalysisPlanValidator().validate(
            draft=draft,
            context=context,
        )
        codes = {issue.code for issue in report.errors}

        self.assertIn("column_not_found", codes)
        self.assertIn("native_executor_required", codes)

    def test_read_only_modes_cannot_mutate_workbooks(self) -> None:
        context = _context(mode=AnalysisMode.ANALYSE)
        report = AnalysisPlanValidator().validate(
            draft=_draft(context, _proposal()),
            context=context,
        )

        codes = {issue.code for issue in report.errors}
        self.assertIn("analyse_mode_workbook_write_forbidden", codes)
        self.assertIn("workbook_write_requires_edit_mode", codes)

    def test_plan_hash_round_trip_is_stable_and_revision_sensitive(self) -> None:
        context = _context()
        draft = _draft(context, _proposal())
        policy = ApprovalPolicy(
            plan_approval_required=False,
            final_patch_approval_required=True,
            auto_execute_read_only=False,
        )
        first = build_analysis_plan(
            draft=draft,
            user_id=_USER_ID,
            workspace_id=_WORKSPACE_ID,
            revision=1,
            approval_policy=policy,
            diagnostics=PlanDiagnostics(generation_attempt=1, repair_count=0),
            model="test-planner",
        )
        restored = first.model_validate_json(first.model_dump_json())
        second = build_analysis_plan(
            draft=draft,
            user_id=_USER_ID,
            workspace_id=_WORKSPACE_ID,
            revision=2,
            approval_policy=policy,
            diagnostics=PlanDiagnostics(generation_attempt=2, repair_count=1),
            model="test-planner",
        )

        self.assertEqual(first.plan_hash, restored.plan_hash)
        self.assertNotEqual(first.plan_hash, second.plan_hash)


class Phase8PlanningServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_typed_planner_validates_output_and_records_usage(self) -> None:
        planner = TypedAnalysisPlanner(
            _Generator(
                {
                    "parsed": _proposal(),
                    "raw": SimpleNamespace(
                        usage_metadata={"input_tokens": 100, "output_tokens": 25}
                    ),
                    "parsing_error": None,
                }
            ),
            model="test-planner",
        )

        invocation = await planner.propose(_context())

        self.assertIsInstance(invocation.proposal, PlanProposal)
        self.assertEqual(invocation.token_usage.total_tokens, 125)
        self.assertIsNotNone(invocation.stage_usage)
        self.assertEqual(invocation.stage_usage.stage, "planning")
        self.assertEqual(invocation.stage_usage.usage.total_tokens, 125)

    async def test_typed_planner_normalizes_harmless_provider_formatting(
        self,
    ) -> None:
        payload = _proposal().model_dump(mode="json")
        step = payload["steps"][0]
        step["estimate"]["rows_scanned"] = "100.0"
        step["estimate"]["output_rows"] = "unknown"
        target = payload["write_intents"][0]["target"]
        target["workbook_revision"] = str(target.pop("base_workbook_revision"))
        planner = TypedAnalysisPlanner(
            _Generator({"parsed": payload, "raw": None, "parsing_error": None}),
            model="test-planner",
        )

        invocation = await planner.propose(_context())
        proposal = invocation.proposal

        self.assertEqual(proposal.steps[0].estimate.rows_scanned, 100)
        self.assertIsNone(proposal.steps[0].estimate.output_rows)
        self.assertEqual(proposal.steps[0].predicate.kind, "compare")
        self.assertEqual(
            proposal.write_intents[0].target.base_workbook_revision,
            12,
        )

    async def test_typed_planner_normalizes_generated_column_labels_to_keys(
        self,
    ) -> None:
        payload = _proposal(with_write=False).model_dump(mode="json")
        payload["steps"][0]["expected_schema"][0]["key"] = "Company Name"
        planner = TypedAnalysisPlanner(
            _Generator({"parsed": payload, "raw": None, "parsing_error": None}),
            model="test-planner",
        )

        invocation = await planner.propose(_context())

        self.assertEqual(
            invocation.proposal.steps[0].expected_schema[0].key,
            "company_name",
        )

    async def test_typed_planner_never_exposes_raw_invalid_output(self) -> None:
        planner = TypedAnalysisPlanner(
            _Generator(
                {
                    "parsed": None,
                    "raw": SimpleNamespace(
                        content="{",
                        usage_metadata={"input_tokens": 5, "output_tokens": 2},
                    ),
                    "parsing_error": ValueError("provider-secret-value"),
                }
            )
        )

        with self.assertRaises(PlannerOutputError) as captured:
            await planner.propose(_context())

        self.assertNotIn("provider-secret-value", str(captured.exception))
        self.assertEqual(captured.exception.token_usage.total_tokens, 7)
        self.assertEqual(captured.exception.stage_usage.stage, "planning")

    async def test_planner_timeout_becomes_privacy_safe_structured_failure(
        self,
    ) -> None:
        context = _context()
        service = AnalysisPlanningService(
            repository=_PlanningRepository(),
            state_machine=object(),
            context_builder=_ContextBuilder(context),
            planner=TypedAnalysisPlanner(_FailingGenerator()),
        )

        result = await service.create_plan(
            run=_run(context),
            dataset_handles=(),
            requirements=_requirements(),
            profiles=object(),
            normalization=object(),
        )

        self.assertEqual(result.outcome, PlanningOutcome.FAILED)
        self.assertEqual(result.errors[0].code, "planner_unavailable")
        self.assertNotIn("provider request", str(result.errors))
        self.assertEqual(
            result.token_usage_by_stage["planning"].call_count,
            1,
        )

    async def test_server_canonicalizes_untrusted_step_provenance(self) -> None:
        context = _context()
        proposal = _proposal()
        forged_step = proposal.steps[0].model_copy(
            update={
                "provenance": StepProvenance(
                    source_dataset_ids=("forged-dataset",),
                    source_versions=(_HASH_C,),
                    description="Untrusted model claim.",
                )
            }
        )
        forged = proposal.model_copy(update={"steps": (forged_step,)})
        service = AnalysisPlanningService(
            repository=_PlanningRepository(),
            state_machine=object(),
            context_builder=_ContextBuilder(context),
            planner=_Planner((forged,)),
        )

        result = await service.create_plan(
            run=_run(context),
            dataset_handles=(),
            requirements=_requirements(),
            profiles=object(),
            normalization=object(),
        )

        provenance = result.plan.steps[0].provenance
        self.assertEqual(provenance.source_dataset_ids, ("source_dataset_1",))
        self.assertEqual(provenance.source_versions, (_HASH_A,))
        self.assertNotIn("forged", provenance.description.casefold())

    async def test_exactly_one_validator_guided_repair_is_allowed(self) -> None:
        context = _context()
        planner = _Planner(
            (
                _proposal(column_key="invented_revenue"),
                _proposal(),
            )
        )
        repository = _PlanningRepository()
        service = AnalysisPlanningService(
            repository=repository,
            state_machine=object(),
            context_builder=_ContextBuilder(context),
            planner=planner,
        )

        result = await service.create_plan(
            run=_run(context),
            dataset_handles=(),
            requirements=_requirements(),
            profiles=object(),
            normalization=object(),
        )

        self.assertEqual(result.outcome, PlanningOutcome.PLAN_READY)
        self.assertEqual(len(result.reports), 2)
        self.assertEqual(result.plan.revision, 1)
        self.assertEqual(result.plan.diagnostics.repair_count, 1)
        self.assertGreater(result.plan.diagnostics.validation_error_count, 0)
        self.assertEqual(planner.propose_calls, 1)
        self.assertEqual(planner.repair_calls, 1)

    async def test_second_invalid_plan_requires_clarification_without_third_call(
        self,
    ) -> None:
        context = _context()
        planner = _Planner(
            (
                _proposal(column_key="invented_revenue"),
                _proposal(column_key="still_invented"),
            )
        )
        service = AnalysisPlanningService(
            repository=_PlanningRepository(),
            state_machine=object(),
            context_builder=_ContextBuilder(context),
            planner=planner,
        )

        result = await service.create_plan(
            run=_run(context),
            dataset_handles=(),
            requirements=_requirements(),
            profiles=object(),
            normalization=object(),
        )

        self.assertEqual(
            result.outcome,
            PlanningOutcome.CLARIFICATION_REQUIRED,
        )
        self.assertIsNone(result.plan)
        self.assertEqual(planner.propose_calls, 1)
        self.assertEqual(planner.repair_calls, 1)

    async def test_persisted_matching_plan_skips_llm(self) -> None:
        context = _context()
        repository = _PlanningRepository()
        repository.plan = build_analysis_plan(
            draft=_draft(context, _proposal()),
            user_id=_USER_ID,
            workspace_id=_WORKSPACE_ID,
            revision=1,
            approval_policy=ApprovalPolicy(
                plan_approval_required=False,
                final_patch_approval_required=True,
                auto_execute_read_only=False,
            ),
            diagnostics=PlanDiagnostics(generation_attempt=1, repair_count=0),
            model="test-planner",
        )
        planner = _Planner((_proposal(),))
        service = AnalysisPlanningService(
            repository=repository,
            state_machine=object(),
            context_builder=_ContextBuilder(context),
            planner=planner,
        )

        result = await service.create_plan(
            run=_run(context),
            dataset_handles=(),
            requirements=_requirements(),
            profiles=object(),
            normalization=object(),
        )

        self.assertEqual(result.outcome, PlanningOutcome.PLAN_READY)
        self.assertEqual(planner.propose_calls, 0)


class Phase8HumanApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.database = _Database()
        self.clock = _Clock()
        self.run_store = MongoAnalysisRunStore(self.database)
        self.state_machine = AnalysisRunStateMachine(
            self.run_store,
            clock=self.clock,
        )
        self.plan_repository = MongoAnalysisPlanRepository(
            self.database,
            capabilities=NATIVE_ENGINE_READY,
        )
        self.context = _context(capabilities=NATIVE_ENGINE_READY)
        self.plan = _approval_plan(self.context)
        self.service = AnalysisPlanningService(
            repository=self.plan_repository,
            state_machine=self.state_machine,
            context_builder=_ContextBuilder(self.context),
            planner=_Planner((_proposal(),)),
        )
        await self.state_machine.create_run(run=_run(self.context))
        await self.plan_repository.create_plan(self.plan)
        await self.state_machine.transition(
            user_id=_USER_ID,
            run_id=self.context.run_id,
            target_status=AnalysisRunStatus.ACTIVE,
            target_phase=AnalysisRunPhase.PLANNING,
            outcome=None,
            event_type=AnalysisEventType.PLANNING_STARTED,
            deduplication_key="planning-started",
        )
        await self.state_machine.transition(
            user_id=_USER_ID,
            run_id=self.context.run_id,
            target_status=AnalysisRunStatus.WAITING,
            target_phase=AnalysisRunPhase.APPROVAL,
            outcome=AnalysisRunOutcome.PLAN_READY,
            event_type=AnalysisEventType.PLAN_APPROVAL_REQUIRED,
            deduplication_key="approval-required",
            summary_updates={
                "current_plan_id": self.plan.plan_id,
                "current_plan_revision": self.plan.revision,
                "current_plan_hash": self.plan.plan_hash,
                "plan_approval_status": RunApprovalStatus.PENDING,
            },
        )

    def _command(
        self,
        *,
        decision: str = "approve",
        decision_id: str | None = None,
    ) -> PlanApprovalCommand:
        return PlanApprovalCommand(
            decision=decision,
            plan_id=self.plan.plan_id,
            expected_revision=self.plan.revision,
            expected_plan_hash=self.plan.plan_hash,
            expected_input_signature=self.plan.input_signature,
            workbook_guards=self.context.workbook_guards,
            decision_id=decision_id or str(uuid4()),
        )

    async def test_plan_approval_updates_plan_run_and_event_atomically(self) -> None:
        command = self._command()

        approved = await self.service.decide_plan(
            user_id=_USER_ID,
            run_id=self.context.run_id,
            command=command,
        )
        replay = await self.service.decide_plan(
            user_id=_USER_ID,
            run_id=self.context.run_id,
            command=command,
        )
        run = await self.state_machine.require_run(
            user_id=_USER_ID,
            run_id=self.context.run_id,
        )
        events = await self.run_store.list_events(
            user_id=_USER_ID,
            run_id=self.context.run_id,
            limit=100,
        )

        self.assertEqual(approved.approval.status, PlanApprovalStatus.APPROVED)
        self.assertEqual(replay.approval.decision_id, command.decision_id)
        self.assertEqual(run.status, AnalysisRunStatus.WAITING)
        self.assertEqual(run.phase, AnalysisRunPhase.EXECUTION)
        self.assertEqual(run.outcome, AnalysisRunOutcome.QUEUED_FOR_EXECUTION)
        self.assertIsNone(run.completed_at)
        self.assertEqual(run.plan_approval_status, RunApprovalStatus.APPROVED)
        self.assertEqual(
            sum(
                event.event_type == AnalysisEventType.PLAN_APPROVED
                for event in events
            ),
            1,
        )

    async def test_rejection_is_a_durable_human_decision(self) -> None:
        rejected = await self.service.decide_plan(
            user_id=_USER_ID,
            run_id=self.context.run_id,
            command=self._command(decision="reject"),
        )
        run = await self.state_machine.require_run(
            user_id=_USER_ID,
            run_id=self.context.run_id,
        )

        self.assertEqual(rejected.approval.status, PlanApprovalStatus.REJECTED)
        self.assertEqual(run.outcome, AnalysisRunOutcome.REJECTED)
        self.assertEqual(run.plan_approval_status, RunApprovalStatus.REJECTED)

    async def test_plan_decision_rolls_back_if_event_append_fails(self) -> None:
        self.database.collections["analysis_run_events"].fail_next_insert = True

        with self.assertRaises(AnalysisPlanRepositoryError):
            await self.service.decide_plan(
                user_id=_USER_ID,
                run_id=self.context.run_id,
                command=self._command(),
            )

        plan = await self.plan_repository.get_plan(
            user_id=_USER_ID,
            run_id=self.context.run_id,
            plan_id=self.plan.plan_id,
        )
        run = await self.state_machine.require_run(
            user_id=_USER_ID,
            run_id=self.context.run_id,
        )
        self.assertEqual(plan.approval.status, PlanApprovalStatus.PENDING)
        self.assertEqual(run.status, AnalysisRunStatus.WAITING)
        self.assertEqual(run.plan_approval_status, RunApprovalStatus.PENDING)

    async def test_write_reservation_is_atomic_and_released_on_rejection(self) -> None:
        other_context = _context()
        other_plan = _approval_plan(other_context)

        with self.assertRaises(AnalysisPlanConflictError):
            await self.plan_repository.create_plan(other_plan)

        await self.service.decide_plan(
            user_id=_USER_ID,
            run_id=self.context.run_id,
            command=self._command(decision="reject"),
        )
        persisted = await self.plan_repository.create_plan(other_plan)

        self.assertEqual(persisted.plan_id, other_plan.plan_id)

    async def test_cancelled_or_stale_workbook_cannot_be_approved(self) -> None:
        await self.state_machine.request_cancellation(
            user_id=_USER_ID,
            run_id=self.context.run_id,
        )
        reservations = await self.plan_repository.list_reserved_write_targets(
            user_id=_USER_ID,
            workspace_id=_WORKSPACE_ID,
            exclude_run_id=str(uuid4()),
        )
        cancelled_plan = await self.plan_repository.get_plan(
            user_id=_USER_ID,
            run_id=self.context.run_id,
            plan_id=self.plan.plan_id,
        )
        self.assertFalse(reservations)
        self.assertFalse(cancelled_plan.reservation_active)
        with self.assertRaises(AnalysisPlanConflictError):
            await self.service.decide_plan(
                user_id=_USER_ID,
                run_id=self.context.run_id,
                command=self._command(),
            )

        stale_guard = self.context.workbook_guards[0].model_copy(
            update={"workbook_revision": 13}
        )
        stale_command = self._command().model_copy(
            update={"workbook_guards": (stale_guard,)}
        )
        with self.assertRaises(AnalysisPlanConflictError):
            await self.service.decide_plan(
                user_id=_USER_ID,
                run_id=self.context.run_id,
                command=stale_command,
            )

    async def test_exact_patch_is_always_guarded_by_final_approval(self) -> None:
        await self.service.decide_plan(
            user_id=_USER_ID,
            run_id=self.context.run_id,
            command=self._command(),
        )
        proposal = FinalPatchProposal(
            patch_id=str(uuid4()),
            run_id=self.context.run_id,
            user_id=_USER_ID,
            workspace_id=_WORKSPACE_ID,
            plan_id=self.plan.plan_id,
            plan_revision=self.plan.revision,
            plan_hash=self.plan.plan_hash,
            input_signature=self.plan.input_signature,
            patch_hash=_HASH_C,
            patch_artifact_version_id="patch-artifact-version-1",
            workbook_guards=self.context.workbook_guards,
            impact=PatchImpactSummary(cells_written=100),
            approval=PlanApprovalRecord(
                status=PlanApprovalStatus.PENDING,
                requested_at=datetime.now(timezone.utc),
            ),
        )
        await self.service.register_patch_proposal(proposal)
        command = FinalPatchApprovalCommand(
            decision="approve",
            patch_id=proposal.patch_id,
            expected_patch_hash=proposal.patch_hash,
            expected_plan_hash=proposal.plan_hash,
            workbook_guards=proposal.workbook_guards,
            decision_id=str(uuid4()),
        )

        approved = await self.service.decide_patch(
            user_id=_USER_ID,
            run_id=self.context.run_id,
            command=command,
        )

        self.assertEqual(approved.approval.status, PlanApprovalStatus.APPROVED)


class Phase8WorkerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_validated_plan_is_queued_without_completing_the_run(self) -> None:
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
        planning_service = _WorkerPlanningService(
            PlanningExecutionResult(
                outcome=PlanningOutcome.PLAN_READY,
                plan=plan,
                admission=ExecutionAdmission.QUEUE,
                reports=(PlanValidationReport(),),
            )
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
            planning_service=planning_service,
            config=AnalysisWorkerConfig(
                concurrency=1,
                poll_seconds=0.01,
                lease_seconds=30,
                renew_seconds=10,
                recovery_batch_size=10,
            ),
            worker_id="worker-phase9-queue",
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
        self.assertEqual(current.status, AnalysisRunStatus.WAITING)
        self.assertEqual(current.phase, AnalysisRunPhase.EXECUTION)
        self.assertEqual(
            current.outcome,
            AnalysisRunOutcome.QUEUED_FOR_EXECUTION,
        )
        self.assertIsNone(current.completed_at)
        self.assertIsNone(current.worker_id)
        self.assertEqual(events[-1].event_type, AnalysisEventType.EXECUTION_QUEUED)

    async def test_worker_rejects_an_injected_legacy_plan_before_queueing(self) -> None:
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
        current = build_analysis_plan(
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
        legacy_data = current.model_dump(mode="python")
        legacy_data["plan_version"] = "1.0"
        legacy_data["plan_hash"] = _HASH_A
        legacy = AnalysisPlan.model_validate(legacy_data)
        planning_service = _WorkerPlanningService(
            PlanningExecutionResult(
                outcome=PlanningOutcome.PLAN_READY,
                plan=legacy,
                reports=(PlanValidationReport(),),
            )
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
            planning_service=planning_service,
            config=AnalysisWorkerConfig(
                concurrency=1,
                poll_seconds=0.01,
                lease_seconds=30,
                renew_seconds=10,
                recovery_batch_size=10,
            ),
            worker_id="worker-legacy-admission",
        )

        await worker._process_candidate(run)

        persisted = await state_machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        events = await store.list_events(
            user_id=run.user_id,
            run_id=run.run_id,
            limit=100,
        )
        self.assertEqual(persisted.status, AnalysisRunStatus.FAILED)
        self.assertEqual(persisted.outcome, AnalysisRunOutcome.FAILED)
        self.assertEqual(events[-1].event_type, AnalysisEventType.RUN_FAILED)
        self.assertEqual(
            events[-1].payload["code"],
            "plan_execution_admission_rejected",
        )

    async def test_phase7_preparation_flows_into_durable_approval_wait(self) -> None:
        database = _Database()
        clock = _Clock()
        store = MongoAnalysisRunStore(database)
        state_machine = AnalysisRunStateMachine(
            store,
            clock=clock,
            maximum_lease_seconds=300,
        )
        dataset = _dataset_handle()
        initial = _dataset_run(clock, dataset).model_copy(
            update={"mode": AnalysisMode.EDIT}
        )
        run = (await state_machine.create_run(run=initial)).run
        context = _context(run_id=run.run_id)
        plan = _approval_plan(context)
        planning_service = _WorkerPlanningService(
            PlanningExecutionResult(
                outcome=PlanningOutcome.APPROVAL_REQUIRED,
                plan=plan,
                reports=(PlanValidationReport(),),
            )
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
            planning_service=planning_service,
            config=AnalysisWorkerConfig(
                concurrency=1,
                poll_seconds=0.01,
                lease_seconds=30,
                renew_seconds=10,
                recovery_batch_size=10,
            ),
            worker_id="worker-phase8-planning",
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
        event_types = tuple(event.event_type for event in events)
        self.assertEqual(planning_service.calls, 1)
        self.assertEqual(current.status, AnalysisRunStatus.WAITING)
        self.assertEqual(current.phase, AnalysisRunPhase.APPROVAL)
        self.assertEqual(current.outcome, AnalysisRunOutcome.PLAN_READY)
        self.assertEqual(current.current_plan_hash, plan.plan_hash)
        self.assertEqual(current.plan_approval_status, RunApprovalStatus.PENDING)
        self.assertIsNone(current.worker_id)
        self.assertIn(AnalysisEventType.PLANNING_STARTED, event_types)
        self.assertIn(AnalysisEventType.PLAN_VALIDATION_STARTED, event_types)
        self.assertEqual(
            event_types[-1],
            AnalysisEventType.PLAN_APPROVAL_REQUIRED,
        )


if __name__ == "__main__":
    unittest.main()
