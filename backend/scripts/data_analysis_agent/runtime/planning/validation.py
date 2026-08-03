from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from ..models.plans import (
    AggregateStep,
    AnalysisPlanDraft,
    ApprovalPolicy,
    ApprovalReason,
    ComparisonOperator,
    ComposeResponseStep,
    DeduplicateStep,
    DeriveColumnStep,
    FillMissingStep,
    FilterRowsStep,
    GenerateDatasetStep,
    JoinStep,
    NullPredicate,
    PlanColumn,
    PlanDataType,
    PlanExecutor,
    PlanStep,
    PredicateValueType,
    PivotStep,
    RenameColumnsStep,
    SelectColumnsStep,
    SetPredicate,
    SortRowsStep,
    StatisticalTestStep,
    TrainModelStep,
    UnpivotStep,
    VisualizationStep,
    WorkbookWriteIntent,
    compute_input_signature,
    step_input_aliases,
)
from ..models.runs import AnalysisMode
from ..models.workbook import a1_ranges_overlap
from .context import PlanningContext
from .contracts import (
    PlanValidationIssue,
    PlanValidationLayer,
    PlanValidationReport,
    PlanValidationSeverity,
)


_NUMERIC_TYPES = frozenset(
    {
        PlanDataType.INTEGER,
        PlanDataType.NUMBER,
        PlanDataType.DECIMAL,
        PlanDataType.CURRENCY,
        PlanDataType.PERCENTAGE,
    }
)
_ORDERABLE_TYPES = _NUMERIC_TYPES | {
    PlanDataType.DATE,
    PlanDataType.PERIOD,
}
_SUPERVISED_MODELS = frozenset(
    {
        "linear_regression",
        "logistic_regression",
        "decision_tree",
        "random_forest",
        "knn",
    }
)
_NATIVE_ONLY_STEPS = (
    FilterRowsStep,
    SortRowsStep,
    SelectColumnsStep,
    RenameColumnsStep,
    FillMissingStep,
    DeduplicateStep,
    AggregateStep,
    JoinStep,
    PivotStep,
    UnpivotStep,
)
_SPECIAL_PYTHON_CHARTS = frozenset(
    {
        "knn_decision_boundary",
        "cluster_plot",
        "correlation_matrix",
    }
)


class AnalysisPlanValidator:
    """Seven deterministic validation layers over a typed untrusted plan."""

    def validate(
        self,
        *,
        draft: AnalysisPlanDraft,
        context: PlanningContext,
        conflicting_write_targets: frozenset[str] = frozenset(),
    ) -> PlanValidationReport:
        issues: list[PlanValidationIssue] = []
        self._validate_structural(draft, context, issues)
        schemas, producers, lineages, row_counts = self._resolve_aliases(
            draft,
            issues,
        )
        self._validate_referential(
            draft,
            context,
            schemas,
            producers,
            issues,
        )
        self._validate_types_and_units(draft, schemas, issues)
        self._validate_execution_policy(draft, context, issues)
        self._validate_resources(draft, context, row_counts, issues)
        self._validate_concurrency(
            draft,
            context,
            conflicting_write_targets,
            issues,
        )
        self._validate_provenance(draft, lineages, issues)
        return PlanValidationReport(issues=tuple(_deduplicate_issues(issues)))

    @staticmethod
    def _validate_structural(
        draft: AnalysisPlanDraft,
        context: PlanningContext,
        issues: list[PlanValidationIssue],
    ) -> None:
        if draft.run_id != context.run_id or draft.mode != context.mode:
            issues.append(
                _error(
                    "plan_run_context_mismatch",
                    PlanValidationLayer.STRUCTURAL,
                    "Plan run identity or mode does not match its durable run.",
                    repairable=False,
                )
            )
        step_ids = {step.step_id for step in draft.steps}
        for index, step in enumerate(draft.steps):
            for dependency in step.depends_on:
                if dependency not in step_ids:
                    issues.append(
                        _error(
                            "unknown_step_dependency",
                            PlanValidationLayer.STRUCTURAL,
                            f"Step '{step.step_id}' depends on an unknown step.",
                            path=f"steps.{index}.depends_on",
                        )
                    )
                if dependency == step.step_id:
                    issues.append(
                        _error(
                            "self_step_dependency",
                            PlanValidationLayer.STRUCTURAL,
                            f"Step '{step.step_id}' cannot depend on itself.",
                            path=f"steps.{index}.depends_on",
                        )
                    )
        graph = {step.step_id: step.depends_on for step in draft.steps}
        if _has_cycle(graph):
            issues.append(
                _error(
                    "cyclic_step_dependencies",
                    PlanValidationLayer.STRUCTURAL,
                    "The execution plan contains a dependency cycle.",
                    path="steps",
                )
            )
        capabilities = context.capabilities
        for index, step in enumerate(draft.steps):
            supported = {
                PlanExecutor.NATIVE: capabilities.native,
                PlanExecutor.PYTHON: capabilities.python,
                PlanExecutor.FRONTEND: capabilities.frontend,
            }[step.executor]
            if not supported:
                issues.append(
                    _error(
                        "executor_unavailable",
                        PlanValidationLayer.STRUCTURAL,
                        f"Executor '{step.executor.value}' is unavailable.",
                        path=f"steps.{index}.executor",
                    )
                )
            if step.network_access:
                issues.append(
                    _error(
                        "external_network_forbidden",
                        PlanValidationLayer.STRUCTURAL,
                        "Planning and execution cannot request external network access.",
                        path=f"steps.{index}.network_access",
                        repairable=False,
                    )
                )
            if isinstance(step, FilterRowsStep) and (
                step.estimate.output_rows is not None
            ):
                issues.append(
                    _error(
                        "filter_output_rows_must_be_unknown",
                        PlanValidationLayer.STRUCTURAL,
                        "A filter's exact output row count is unknown before execution.",
                        path=f"steps.{index}.estimate.output_rows",
                    )
                )

    @staticmethod
    def _resolve_aliases(
        draft: AnalysisPlanDraft,
        issues: list[PlanValidationIssue],
    ) -> tuple[
        dict[str, tuple[PlanColumn, ...]],
        dict[str, str | None],
        dict[str, frozenset[tuple[str, str]]],
        dict[str, int | None],
    ]:
        schemas = {
            dataset.alias: dataset.columns for dataset in draft.input_datasets
        }
        producers: dict[str, str | None] = {
            dataset.alias: None for dataset in draft.input_datasets
        }
        lineages = {
            dataset.alias: frozenset(
                (item.source_dataset_id, item.source_version)
                for item in dataset.provenance
            )
            for dataset in draft.input_datasets
        }
        row_counts: dict[str, int | None] = {
            dataset.alias: dataset.row_count for dataset in draft.input_datasets
        }
        for index, step in enumerate(draft.steps):
            input_aliases = step_input_aliases(step)
            for alias in input_aliases:
                if alias not in schemas:
                    issues.append(
                        _error(
                            "output_referenced_before_creation",
                            PlanValidationLayer.STRUCTURAL,
                            f"Step '{step.step_id}' references unavailable alias '{alias}'.",
                            path=f"steps.{index}",
                        )
                    )
                    continue
                producer = producers.get(alias)
                if producer is not None and producer not in step.depends_on:
                    issues.append(
                        _error(
                            "missing_data_dependency",
                            PlanValidationLayer.STRUCTURAL,
                            f"Step '{step.step_id}' must depend on producer '{producer}'.",
                            path=f"steps.{index}.depends_on",
                        )
                    )
            schemas[step.output_alias] = step.expected_schema
            producers[step.output_alias] = step.step_id
            if isinstance(step, GenerateDatasetStep):
                lineages[step.output_alias] = frozenset()
                row_counts[step.output_alias] = step.row_count
            else:
                lineage: set[tuple[str, str]] = set()
                for alias in input_aliases:
                    lineage.update(lineages.get(alias, frozenset()))
                lineages[step.output_alias] = frozenset(lineage)
                row_counts[step.output_alias] = _maximum_output_rows(
                    step,
                    row_counts,
                )
        return schemas, producers, lineages, row_counts

    @staticmethod
    def _validate_referential(
        draft: AnalysisPlanDraft,
        context: PlanningContext,
        schemas: dict[str, tuple[PlanColumn, ...]],
        producers: dict[str, str | None],
        issues: list[PlanValidationIssue],
    ) -> None:
        expected = {
            (dataset.dataset_id, dataset.dataset_version): dataset
            for dataset in context.input_datasets
        }
        supplied = {
            (dataset.dataset_id, dataset.dataset_version): dataset
            for dataset in draft.input_datasets
        }
        if expected != supplied:
            issues.append(
                _error(
                    "input_dataset_versions_changed",
                    PlanValidationLayer.REFERENTIAL,
                    "Plan input datasets do not match trusted prepared versions.",
                    path="input_datasets",
                    repairable=False,
                )
            )
        for index, step in enumerate(draft.steps):
            for alias, columns in _step_column_references(step).items():
                schema = _schema_map(schemas.get(alias, ()))
                for column in columns:
                    if column not in schema:
                        issues.append(
                            _error(
                                "column_not_found",
                                PlanValidationLayer.REFERENTIAL,
                                f"Column key '{column}' does not exist in '{alias}'.",
                                path=f"steps.{index}",
                            )
                        )
            _validate_expected_schema(step, schemas, index, issues)
            _validate_assertions(step, index, issues)
        available_aliases = set(producers)
        for index, intent in enumerate(draft.write_intents):
            if intent.input_alias not in available_aliases:
                issues.append(
                    _error(
                        "write_input_not_found",
                        PlanValidationLayer.REFERENTIAL,
                        f"Write intent references unavailable alias '{intent.input_alias}'.",
                        path=f"write_intents.{index}.input_alias",
                    )
                )
        for index, artifact in enumerate(draft.expected_artifacts):
            if artifact.source_alias not in available_aliases:
                issues.append(
                    _error(
                        "artifact_source_not_found",
                        PlanValidationLayer.REFERENTIAL,
                        f"Expected artifact references unavailable alias '{artifact.source_alias}'.",
                        path=f"expected_artifacts.{index}.source_alias",
                    )
                )

    @staticmethod
    def _validate_types_and_units(
        draft: AnalysisPlanDraft,
        schemas: dict[str, tuple[PlanColumn, ...]],
        issues: list[PlanValidationIssue],
    ) -> None:
        for index, step in enumerate(draft.steps):
            if isinstance(step, FilterRowsStep):
                schema = _schema_map(schemas.get(step.input_alias, ()))
                for predicate in step.predicates:
                    if isinstance(predicate, NullPredicate):
                        continue
                    column = schema.get(predicate.column_key)
                    if column is None:
                        continue
                    _validate_predicate(column, predicate, index, issues)
            elif isinstance(step, FillMissingStep):
                schema = _schema_map(schemas.get(step.input_alias, ()))
                for rule in step.rules:
                    column = schema.get(rule.column_key)
                    if column is None:
                        continue
                    if rule.strategy in {"mean", "median"} and (
                        column.data_type not in _NUMERIC_TYPES
                    ):
                        issues.append(
                            _type_error(
                                "numeric_fill_on_non_numeric_column",
                                f"Fill strategy '{rule.strategy}' requires a numeric column.",
                                index,
                            )
                        )
                    if rule.strategy == "constant" and not _literal_matches_type(
                        rule.value,
                        column.data_type,
                    ):
                        issues.append(
                            _type_error(
                                "constant_fill_type_mismatch",
                                "Constant fill value does not match its column type.",
                                index,
                            )
                        )
            elif isinstance(step, AggregateStep):
                schema = _schema_map(schemas.get(step.input_alias, ()))
                for metric in step.metrics:
                    column = schema.get(metric.input_column_key)
                    if column is None:
                        continue
                    if metric.function not in {"count", "count_distinct"} and (
                        column.data_type not in _NUMERIC_TYPES
                    ):
                        issues.append(
                            _type_error(
                                "aggregation_type_mismatch",
                                f"Aggregation '{metric.function}' requires numeric input.",
                                index,
                            )
                        )
                    output = metric.output_column
                    if metric.function in {"count", "count_distinct"}:
                        valid_output = (
                            output.data_type == PlanDataType.INTEGER
                            and output.unit is None
                        )
                    else:
                        valid_output = (
                            output.data_type in _NUMERIC_TYPES
                            and output.unit == column.unit
                        )
                    if not valid_output:
                        issues.append(
                            _type_error(
                                "aggregation_output_type_mismatch",
                                "Aggregate output type or unit is incompatible.",
                                index,
                            )
                        )
            elif isinstance(step, JoinStep):
                left = _schema_map(schemas.get(step.left_alias, ()))
                right = _schema_map(schemas.get(step.right_alias, ()))
                for pair in step.keys:
                    left_column = left.get(pair.left_column_key)
                    right_column = right.get(pair.right_column_key)
                    if left_column is None or right_column is None:
                        continue
                    if not _compatible_types(left_column, right_column):
                        issues.append(
                            _type_error(
                                "join_key_type_mismatch",
                                "Join key columns have incompatible types or units.",
                                index,
                            )
                        )
            elif isinstance(step, DeriveColumnStep):
                if (
                    step.expression_language == "python"
                    and step.executor != PlanExecutor.PYTHON
                ) or (
                    step.expression_language != "python"
                    and step.executor == PlanExecutor.PYTHON
                ):
                    issues.append(
                        _type_error(
                            "derive_executor_mismatch",
                            "Derived-column language does not match its executor.",
                            index,
                        )
                    )
            elif isinstance(step, PivotStep):
                schema = _schema_map(schemas.get(step.input_alias, ()))
                value_column = schema.get(step.value_column)
                if (
                    step.aggregation != "count"
                    and value_column is not None
                    and value_column.data_type not in _NUMERIC_TYPES
                ):
                    issues.append(
                        _type_error(
                            "pivot_value_type_mismatch",
                            "Pivot aggregation requires a numeric value column.",
                            index,
                        )
                    )
            elif isinstance(step, UnpivotStep):
                schema = _schema_map(schemas.get(step.input_alias, ()))
                values = tuple(
                    schema[key]
                    for key in step.value_columns
                    if key in schema
                )
                if (
                    step.variable_column.data_type != PlanDataType.STRING
                    or step.variable_column.unit is not None
                ):
                    issues.append(
                        _type_error(
                            "unpivot_variable_type_mismatch",
                            "Unpivot variable columns must be unitless strings.",
                            index,
                        )
                    )
                if values and (
                    any(not _compatible_types(values[0], item) for item in values[1:])
                    or not _compatible_types(values[0], step.value_column)
                ):
                    issues.append(
                        _type_error(
                            "unpivot_value_type_mismatch",
                            "Unpivot value columns must have compatible types and units.",
                            index,
                        )
                    )
            elif isinstance(step, StatisticalTestStep):
                schema = _schema_map(schemas.get(step.input_alias, ()))
                if step.test != "chi_square":
                    _require_numeric_columns(
                        schema,
                        step.feature_columns,
                        "statistical_test_type_mismatch",
                        index,
                        issues,
                    )
            elif isinstance(step, TrainModelStep):
                schema = _schema_map(schemas.get(step.input_alias, ()))
                _require_numeric_columns(
                    schema,
                    step.feature_columns,
                    "model_feature_type_mismatch",
                    index,
                    issues,
                )
                supervised = step.model_type in _SUPERVISED_MODELS
                if supervised != (step.target_column is not None):
                    issues.append(
                        _type_error(
                            "model_target_mismatch",
                            "Supervised models require a target; clustering must not have one.",
                            index,
                        )
                    )
            elif isinstance(step, VisualizationStep):
                schema = _schema_map(schemas.get(step.input_alias, ()))
                numeric_keys = (
                    *((step.x_column,) if step.chart_type == "scatter" else ()),
                    *step.y_columns,
                )
                _require_numeric_columns(
                    schema,
                    numeric_keys,
                    "visualization_type_mismatch",
                    index,
                    issues,
                )

    @staticmethod
    def _validate_execution_policy(
        draft: AnalysisPlanDraft,
        context: PlanningContext,
        issues: list[PlanValidationIssue],
    ) -> None:
        available_packages = set(context.capabilities.supported_python_packages)
        for index, step in enumerate(draft.steps):
            if isinstance(step, _NATIVE_ONLY_STEPS) and (
                step.executor != PlanExecutor.NATIVE
            ):
                issues.append(
                    _error(
                        "native_executor_required",
                        PlanValidationLayer.EXECUTION_POLICY,
                        f"Operation '{step.kind}' must use the native executor.",
                        path=f"steps.{index}.executor",
                    )
                )
            if isinstance(step, (StatisticalTestStep, TrainModelStep)) and (
                step.executor != PlanExecutor.PYTHON
            ):
                issues.append(
                    _error(
                        "python_executor_required",
                        PlanValidationLayer.EXECUTION_POLICY,
                        f"Operation '{step.kind}' requires the Python executor.",
                        path=f"steps.{index}.executor",
                    )
                )
            if isinstance(step, VisualizationStep):
                special = step.chart_type in _SPECIAL_PYTHON_CHARTS
                if special and step.executor != PlanExecutor.PYTHON:
                    issues.append(
                        _error(
                            "special_chart_requires_python",
                            PlanValidationLayer.EXECUTION_POLICY,
                            "The requested analytical chart requires Python.",
                            path=f"steps.{index}.executor",
                        )
                    )
                if not special and step.executor == PlanExecutor.PYTHON:
                    issues.append(
                        _error(
                            "simple_chart_cannot_use_python",
                            PlanValidationLayer.EXECUTION_POLICY,
                            "Standard charts must use a native or frontend executor.",
                            path=f"steps.{index}.executor",
                        )
                    )
            required_packages: set[str] = set()
            if step.executor == PlanExecutor.PYTHON:
                if isinstance(step, TrainModelStep):
                    required_packages.add("scikit-learn")
                elif isinstance(step, StatisticalTestStep):
                    required_packages.add("scipy")
                elif isinstance(step, VisualizationStep):
                    required_packages.add("matplotlib")
                    if step.chart_type in {
                        "knn_decision_boundary",
                        "cluster_plot",
                    }:
                        required_packages.add("scikit-learn")
                elif isinstance(step, DeriveColumnStep):
                    required_packages.add("pandas")
                elif isinstance(step, GenerateDatasetStep):
                    required_packages.add("numpy")
            missing_packages = required_packages.difference(available_packages)
            if missing_packages:
                issues.append(
                    _error(
                        "python_feature_unavailable",
                        PlanValidationLayer.EXECUTION_POLICY,
                        "Python executor is missing required approved packages: "
                        + ", ".join(sorted(missing_packages)),
                        path=f"steps.{index}.executor",
                        repairable=False,
                    )
                )
        workbook_writes = tuple(
            intent
            for intent in draft.write_intents
            if isinstance(intent, WorkbookWriteIntent)
        )
        workbook_write_aliases = {
            intent.input_alias for intent in workbook_writes
        }
        for index, step in enumerate(draft.steps):
            if not isinstance(step, VisualizationStep) or not step.attach_to_workbook:
                continue
            if draft.mode != AnalysisMode.EDIT:
                issues.append(
                    _error(
                        "chart_attachment_requires_edit_mode",
                        PlanValidationLayer.EXECUTION_POLICY,
                        "Attaching a chart to a workbook requires edit mode.",
                        path=f"steps.{index}.attach_to_workbook",
                        repairable=False,
                    )
                )
            if step.output_alias not in workbook_write_aliases:
                issues.append(
                    _error(
                        "chart_attachment_write_intent_missing",
                        PlanValidationLayer.EXECUTION_POLICY,
                        "A workbook chart attachment needs an explicit write intent.",
                        path=f"steps.{index}.attach_to_workbook",
                    )
                )
        if draft.mode == AnalysisMode.ASK and draft.write_intents:
            issues.append(
                _error(
                    "ask_mode_write_forbidden",
                    PlanValidationLayer.EXECUTION_POLICY,
                    "Ask mode cannot create artifacts or modify workbooks.",
                    path="write_intents",
                    repairable=False,
                )
            )
        if draft.mode == AnalysisMode.ANALYSE and workbook_writes:
            issues.append(
                _error(
                    "analyse_mode_workbook_write_forbidden",
                    PlanValidationLayer.EXECUTION_POLICY,
                    "Analyse mode may create artifacts but cannot mutate workbooks.",
                    path="write_intents",
                    repairable=False,
                )
            )
        if draft.mode != AnalysisMode.EDIT and workbook_writes:
            issues.append(
                _error(
                    "workbook_write_requires_edit_mode",
                    PlanValidationLayer.EXECUTION_POLICY,
                    "Workbook write intents require edit mode.",
                    path="write_intents",
                    repairable=False,
                )
            )
        if any(
            isinstance(intent, WorkbookWriteIntent)
            and not intent.requires_final_approval
            for intent in draft.write_intents
        ):
            issues.append(
                _error(
                    "final_patch_approval_required",
                    PlanValidationLayer.EXECUTION_POLICY,
                    "Every workbook patch requires final human approval.",
                    path="write_intents",
                    repairable=False,
                )
            )
        for index, intent in enumerate(workbook_writes):
            target_range = intent.target.exact_target_range_a1
            if (
                target_range is not None
                and a1_ranges_overlap(
                    intent.target.source_range_a1,
                    target_range,
                )
                and not intent.destructive
            ):
                issues.append(
                    _error(
                        "destructive_write_not_declared",
                        PlanValidationLayer.EXECUTION_POLICY,
                        "A target overlapping source cells must be declared destructive.",
                        path=f"write_intents.{index}.destructive",
                    )
                )

    @staticmethod
    def _validate_resources(
        draft: AnalysisPlanDraft,
        context: PlanningContext,
        row_counts: dict[str, int | None],
        issues: list[PlanValidationIssue],
    ) -> None:
        policy = context.resource_policy
        plan_bytes = len(draft.model_dump_json().encode("utf-8"))
        if plan_bytes > policy.max_plan_bytes:
            issues.append(
                _resource_error(
                    "plan_size_limit_exceeded",
                    "Plan document exceeds the configured byte limit.",
                    repairable=False,
                )
            )
        if len(draft.steps) > policy.max_steps:
            issues.append(
                _resource_error(
                    "plan_step_limit_exceeded",
                    "Plan contains too many execution steps.",
                )
            )
        rows_scanned = 0
        cells_written = 0
        total_cost = 0.0
        join_count = 0
        for index, step in enumerate(draft.steps):
            input_rows = sum(
                row_counts.get(alias) or 0 for alias in step_input_aliases(step)
            )
            if step.estimate.rows_scanned < input_rows:
                issues.append(
                    _resource_error(
                        "rows_scanned_underestimated",
                        f"Step '{step.step_id}' underestimates rows scanned.",
                        path=f"steps.{index}.estimate.rows_scanned",
                    )
                )
            rows_scanned += max(step.estimate.rows_scanned, input_rows)
            cells_written += step.estimate.cells_written
            total_cost += step.estimate.estimated_cost_usd
            join_count += isinstance(step, JoinStep)
            if isinstance(step, GenerateDatasetStep) and (
                step.row_count > policy.max_generated_rows
            ):
                issues.append(
                    _resource_error(
                        "generated_row_limit_exceeded",
                        "Generated dataset exceeds the configured row limit.",
                        path=f"steps.{index}.row_count",
                    )
                )
            if step.executor == PlanExecutor.PYTHON:
                if step.estimate.memory_mb > policy.max_python_memory_mb:
                    issues.append(
                        _resource_error(
                            "python_memory_limit_exceeded",
                            "Python step exceeds the memory policy.",
                            path=f"steps.{index}.estimate.memory_mb",
                        )
                    )
                if step.estimate.duration_seconds > policy.max_python_seconds:
                    issues.append(
                        _resource_error(
                            "python_duration_limit_exceeded",
                            "Python step exceeds the duration policy.",
                            path=f"steps.{index}.estimate.duration_seconds",
                        )
                    )
            if step.estimate.chart_cardinality > policy.max_chart_cardinality:
                issues.append(
                    _resource_error(
                        "chart_cardinality_limit_exceeded",
                        "Chart cardinality exceeds the configured limit.",
                        path=f"steps.{index}.estimate.chart_cardinality",
                    )
                )
        for intent in draft.write_intents:
            if not isinstance(intent, WorkbookWriteIntent):
                continue
            schema = next(
                (
                    step.expected_schema
                    for step in draft.steps
                    if step.output_alias == intent.input_alias
                ),
                next(
                    (
                        dataset.columns
                        for dataset in draft.input_datasets
                        if dataset.alias == intent.input_alias
                    ),
                    (),
                ),
            )
            output_rows = row_counts.get(intent.input_alias)
            if output_rows is None:
                output_rows = max(
                    (dataset.row_count for dataset in draft.input_datasets),
                    default=0,
                )
            cells_written += output_rows * len(schema)
        if rows_scanned > policy.max_rows_scanned:
            issues.append(
                _resource_error(
                    "rows_scanned_limit_exceeded",
                    "Plan exceeds the total scanned-row limit.",
                )
            )
        if cells_written > policy.max_cells_written:
            issues.append(
                _resource_error(
                    "cells_written_limit_exceeded",
                    "Plan exceeds the total written-cell limit.",
                )
            )
        if join_count > policy.max_joins:
            issues.append(
                _resource_error(
                    "join_limit_exceeded",
                    "Plan contains too many joins.",
                )
            )
        if total_cost > policy.max_estimated_cost_usd:
            issues.append(
                _resource_error(
                    "estimated_cost_limit_exceeded",
                    "Plan exceeds the configured estimated-cost limit.",
                )
            )

    @staticmethod
    def _validate_concurrency(
        draft: AnalysisPlanDraft,
        context: PlanningContext,
        conflicting_write_targets: frozenset[str],
        issues: list[PlanValidationIssue],
    ) -> None:
        expected_signature = compute_input_signature(context.input_datasets)
        if (
            draft.input_signature != expected_signature
            or context.input_signature != expected_signature
        ):
            issues.append(
                _error(
                    "input_signature_changed",
                    PlanValidationLayer.CONCURRENCY,
                    "Dataset versions changed while the plan was being generated.",
                    path="input_signature",
                    repairable=False,
                )
            )
        guards = {guard.target_key: guard for guard in context.workbook_guards}
        provenance = tuple(
            source
            for dataset in context.input_datasets
            for source in dataset.provenance
        )
        for index, intent in enumerate(draft.write_intents):
            if not isinstance(intent, WorkbookWriteIntent):
                continue
            target_key = (
                f"{intent.target.workbook_id}:{intent.target.worksheet_id}"
            )
            guard = guards.get(target_key)
            if guard is None:
                issues.append(
                    _error(
                        "workbook_target_not_in_context",
                        PlanValidationLayer.CONCURRENCY,
                        "Workbook write target is not part of the active context.",
                        path=f"write_intents.{index}.target",
                        repairable=False,
                    )
                )
                continue
            if (
                intent.target.base_workbook_revision != guard.workbook_revision
                or intent.target.base_snapshot_hash != guard.snapshot_hash
            ):
                issues.append(
                    _error(
                        "workbook_version_changed",
                        PlanValidationLayer.CONCURRENCY,
                        "Workbook revision or source hash changed during planning.",
                        path=f"write_intents.{index}.target",
                        repairable=False,
                    )
                )
            source_matches = any(
                source.workbook_id == intent.target.workbook_id
                and source.worksheet_id == intent.target.worksheet_id
                and source.range_a1 == intent.target.source_range_a1
                and source.snapshot_hash == intent.target.base_snapshot_hash
                for source in provenance
            )
            if not source_matches:
                issues.append(
                    _error(
                        "write_source_range_mismatch",
                        PlanValidationLayer.CONCURRENCY,
                        "Workbook write source does not match immutable evidence.",
                        path=f"write_intents.{index}.target.source_range_a1",
                        repairable=False,
                    )
                )
            if target_key in conflicting_write_targets:
                issues.append(
                    _error(
                        "conflicting_workbook_plan",
                        PlanValidationLayer.CONCURRENCY,
                        "Another pending run targets the same workbook sheet.",
                        path=f"write_intents.{index}.target",
                        repairable=False,
                    )
                )

    @staticmethod
    def _validate_provenance(
        draft: AnalysisPlanDraft,
        lineages: dict[str, frozenset[tuple[str, str]]],
        issues: list[PlanValidationIssue],
    ) -> None:
        for index, step in enumerate(draft.steps):
            declared = frozenset(
                zip(
                    step.provenance.source_dataset_ids,
                    step.provenance.source_versions,
                    strict=True,
                )
            )
            expected = lineages.get(step.output_alias, frozenset())
            if isinstance(step, GenerateDatasetStep):
                if not step.provenance.generated or declared:
                    issues.append(
                        _error(
                            "generated_provenance_invalid",
                            PlanValidationLayer.PROVENANCE,
                            "Generated data must be explicitly marked and source-free.",
                            path=f"steps.{index}.provenance",
                        )
                    )
            elif step.provenance.generated or declared != expected:
                issues.append(
                    _error(
                        "step_provenance_mismatch",
                        PlanValidationLayer.PROVENANCE,
                        f"Step '{step.step_id}' does not preserve exact source lineage.",
                        path=f"steps.{index}.provenance",
                    )
                )


def derive_approval_policy(
    *,
    draft: AnalysisPlanDraft,
    context: PlanningContext,
) -> ApprovalPolicy:
    reasons: list[ApprovalReason] = []
    policy = context.resource_policy
    python_duration = sum(
        step.estimate.duration_seconds
        for step in draft.steps
        if step.executor == PlanExecutor.PYTHON
    )
    total_duration = sum(step.estimate.duration_seconds for step in draft.steps)
    total_cost = sum(step.estimate.estimated_cost_usd for step in draft.steps)
    generated_rows = sum(
        step.row_count
        for step in draft.steps
        if isinstance(step, GenerateDatasetStep)
    )
    if python_duration > policy.plan_approval_python_seconds:
        reasons.append(ApprovalReason.EXPENSIVE_PYTHON)
    if generated_rows > policy.plan_approval_generated_rows:
        reasons.append(ApprovalReason.LARGE_GENERATED_DATASET)
    if total_duration > policy.plan_approval_python_seconds:
        reasons.append(ApprovalReason.LONG_RUNNING)
    if total_cost > policy.plan_approval_cost_usd:
        reasons.append(ApprovalReason.MEANINGFUL_COST)
    workbook_writes = tuple(
        intent
        for intent in draft.write_intents
        if isinstance(intent, WorkbookWriteIntent)
    )
    if any(intent.destructive for intent in workbook_writes):
        reasons.append(ApprovalReason.DESTRUCTIVE_WRITE)
    if any(intent.overwrite_formulas for intent in workbook_writes):
        reasons.append(ApprovalReason.FORMULA_OVERWRITE)
    unique_reasons = tuple(dict.fromkeys(reasons))
    return ApprovalPolicy(
        plan_approval_required=bool(unique_reasons),
        plan_approval_reasons=unique_reasons,
        final_patch_approval_required=bool(workbook_writes),
        auto_execute_read_only=(not unique_reasons and not workbook_writes),
    )


def _step_column_references(step: PlanStep) -> dict[str, tuple[str, ...]]:
    if isinstance(step, FilterRowsStep):
        return {
            step.input_alias: tuple(
                predicate.column_key for predicate in step.predicates
            )
        }
    if isinstance(step, SortRowsStep):
        return {step.input_alias: tuple(key.column_key for key in step.keys)}
    if isinstance(step, SelectColumnsStep):
        return {step.input_alias: step.column_keys}
    if isinstance(step, RenameColumnsStep):
        return {
            step.input_alias: tuple(item.source_key for item in step.renames)
        }
    if isinstance(step, FillMissingStep):
        return {step.input_alias: tuple(rule.column_key for rule in step.rules)}
    if isinstance(step, DeduplicateStep):
        return {step.input_alias: step.key_columns}
    if isinstance(step, DeriveColumnStep):
        return {step.input_alias: step.referenced_columns}
    if isinstance(step, AggregateStep):
        return {
            step.input_alias: (
                *step.group_by,
                *(metric.input_column_key for metric in step.metrics),
            )
        }
    if isinstance(step, JoinStep):
        return {
            step.left_alias: tuple(key.left_column_key for key in step.keys),
            step.right_alias: tuple(key.right_column_key for key in step.keys),
        }
    if isinstance(step, PivotStep):
        return {
            step.input_alias: (
                *step.index_columns,
                step.pivot_column,
                step.value_column,
            )
        }
    if isinstance(step, UnpivotStep):
        return {
            step.input_alias: (*step.id_columns, *step.value_columns)
        }
    if isinstance(step, StatisticalTestStep):
        return {step.input_alias: step.feature_columns}
    if isinstance(step, TrainModelStep):
        return {
            step.input_alias: (
                *step.feature_columns,
                *((step.target_column,) if step.target_column else ()),
            )
        }
    if isinstance(step, VisualizationStep):
        return {
            step.input_alias: tuple(
                item
                for item in (
                    step.x_column,
                    *step.y_columns,
                    step.group_column,
                )
                if item is not None
            )
        }
    return {}


def _validate_expected_schema(
    step: PlanStep,
    schemas: dict[str, tuple[PlanColumn, ...]],
    index: int,
    issues: list[PlanValidationIssue],
) -> None:
    if isinstance(
        step,
        (FilterRowsStep, SortRowsStep, FillMissingStep, DeduplicateStep),
    ):
        source = schemas.get(step.input_alias, ())
        if step.expected_schema != source:
            issues.append(
                _error(
                    "schema_preservation_mismatch",
                    PlanValidationLayer.REFERENTIAL,
                    f"Operation '{step.kind}' must preserve its input schema.",
                    path=f"steps.{index}.expected_schema",
                )
            )
    elif isinstance(step, SelectColumnsStep):
        source = _schema_map(schemas.get(step.input_alias, ()))
        expected = tuple(
            source[key] for key in step.column_keys if key in source
        )
        if step.expected_schema != expected:
            issues.append(
                _error(
                    "selected_schema_mismatch",
                    PlanValidationLayer.REFERENTIAL,
                    "Selected columns and expected schema do not match.",
                    path=f"steps.{index}.expected_schema",
                )
            )
    elif isinstance(step, RenameColumnsStep):
        source = schemas.get(step.input_alias, ())
        renames = {item.source_key: item for item in step.renames}
        output_keys = tuple(item.output_key for item in step.renames)
        expected = tuple(
            column.model_copy(
                update={
                    "key": renames[column.key].output_key,
                    "label": renames[column.key].output_label,
                }
            )
            if column.key in renames
            else column
            for column in source
        )
        if len(output_keys) != len(set(output_keys)):
            issues.append(
                _error(
                    "rename_output_keys_not_unique",
                    PlanValidationLayer.REFERENTIAL,
                    "Renamed output column keys must be unique.",
                    path=f"steps.{index}.renames",
                )
            )
        if step.expected_schema != expected:
            issues.append(
                _error(
                    "renamed_schema_mismatch",
                    PlanValidationLayer.REFERENTIAL,
                    "Renaming must preserve column order, types, and units.",
                    path=f"steps.{index}.expected_schema",
                )
            )
    elif isinstance(step, DeriveColumnStep):
        source = schemas.get(step.input_alias, ())
        expected = (*source, step.output_column)
        if (
            step.output_column.key in _schema_map(source)
            or step.expected_schema != expected
        ):
            issues.append(
                _error(
                    "derived_schema_missing_output",
                    PlanValidationLayer.REFERENTIAL,
                    "Derived output column is missing from expected schema.",
                    path=f"steps.{index}.expected_schema",
                )
            )
    elif isinstance(step, AggregateStep):
        source = _schema_map(schemas.get(step.input_alias, ()))
        expected = (
            *(source[key] for key in step.group_by if key in source),
            *(metric.output_column for metric in step.metrics),
        )
        if step.expected_schema != expected:
            issues.append(
                _error(
                    "aggregate_schema_mismatch",
                    PlanValidationLayer.REFERENTIAL,
                    "Aggregate output must contain group keys and declared metrics.",
                    path=f"steps.{index}.expected_schema",
                )
            )
    elif isinstance(step, UnpivotStep):
        source = _schema_map(schemas.get(step.input_alias, ()))
        expected = (
            *(source[key] for key in step.id_columns if key in source),
            step.variable_column,
            step.value_column,
        )
        if step.expected_schema != expected:
            issues.append(
                _error(
                    "unpivot_schema_mismatch",
                    PlanValidationLayer.REFERENTIAL,
                    "Unpivot output does not match its declared ID/value columns.",
                    path=f"steps.{index}.expected_schema",
                )
            )


def _validate_assertions(
    step: PlanStep,
    index: int,
    issues: list[PlanValidationIssue],
) -> None:
    schema = _schema_map(step.expected_schema)
    for assertion_index, assertion in enumerate(step.assertions):
        missing = tuple(key for key in assertion.columns if key not in schema)
        if missing:
            issues.append(
                _error(
                    "assertion_column_not_found",
                    PlanValidationLayer.REFERENTIAL,
                    "A validation assertion references an unknown output column.",
                    path=(
                        f"steps.{index}.assertions.{assertion_index}.columns"
                    ),
                )
            )
        if (
            assertion.kind.value == "value_range"
            and any(
                schema[key].data_type not in _NUMERIC_TYPES
                for key in assertion.columns
                if key in schema
            )
        ):
            issues.append(
                _type_error(
                    "value_range_assertion_requires_numeric_column",
                    "Value-range assertions require numeric output columns.",
                    index,
                )
            )


def _maximum_output_rows(
    step: PlanStep,
    row_counts: dict[str, int | None],
) -> int | None:
    inputs = tuple(row_counts.get(alias) for alias in step_input_aliases(step))
    if not inputs or any(value is None for value in inputs):
        return None
    known = tuple(int(value) for value in inputs if value is not None)
    if isinstance(step, JoinStep):
        return known[0] * known[1]
    if isinstance(step, UnpivotStep):
        return known[0] * len(step.value_columns)
    if isinstance(step, ComposeResponseStep):
        return max(known, default=0)
    return known[0]


def _validate_predicate(
    column: PlanColumn,
    predicate: object,
    index: int,
    issues: list[PlanValidationIssue],
) -> None:
    expected_type = {
        PredicateValueType.STRING: {PlanDataType.STRING},
        PredicateValueType.NUMBER: _NUMERIC_TYPES,
        PredicateValueType.BOOLEAN: {PlanDataType.BOOLEAN},
        PredicateValueType.DATE: {PlanDataType.DATE, PlanDataType.PERIOD},
        PredicateValueType.CURRENCY: {PlanDataType.CURRENCY},
        PredicateValueType.PERCENTAGE: {PlanDataType.PERCENTAGE},
    }[predicate.value_type]
    if column.data_type not in expected_type:
        issues.append(
            _type_error(
                "predicate_type_mismatch",
                f"Predicate value type does not match column '{column.key}'.",
                index,
            )
        )
    values = (
        predicate.values
        if isinstance(predicate, SetPredicate)
        else (predicate.value,)
    )
    if any(
        not _predicate_literal_matches(value, predicate.value_type)
        for value in values
    ):
        issues.append(
            _type_error(
                "predicate_literal_type_mismatch",
                "Predicate literal does not match its declared value type.",
                index,
            )
        )
    if isinstance(predicate, SetPredicate):
        pass
    elif (
        predicate.operator
        in {
            ComparisonOperator.GT,
            ComparisonOperator.GTE,
            ComparisonOperator.LT,
            ComparisonOperator.LTE,
        }
        and column.data_type not in _ORDERABLE_TYPES
    ):
        issues.append(
            _type_error(
                "ordered_comparison_type_mismatch",
                "Ordered comparisons require numeric, date, or period columns.",
                index,
            )
        )
    elif (
        predicate.operator == ComparisonOperator.CONTAINS
        and column.data_type != PlanDataType.STRING
    ):
        issues.append(
            _type_error(
                "contains_requires_string",
                "Contains comparisons require a string column.",
                index,
            )
        )
    if column.unit and predicate.unit != column.unit:
        issues.append(
            _type_error(
                "predicate_unit_mismatch",
                f"Predicate unit must explicitly match '{column.unit}'.",
                index,
            )
        )
    if not column.unit and predicate.unit:
        issues.append(
            _type_error(
                "unexpected_predicate_unit",
                "Predicate supplies a unit for a unitless column.",
                index,
            )
        )


def _compatible_types(left: PlanColumn, right: PlanColumn) -> bool:
    semantic_numeric = {PlanDataType.CURRENCY, PlanDataType.PERCENTAGE}
    if left.data_type in semantic_numeric or right.data_type in semantic_numeric:
        type_compatible = left.data_type == right.data_type
    else:
        type_compatible = (
            left.data_type == right.data_type
            or left.data_type in _NUMERIC_TYPES
            and right.data_type in _NUMERIC_TYPES
        )
    return type_compatible and left.unit == right.unit


def _predicate_literal_matches(
    value: object,
    value_type: PredicateValueType,
) -> bool:
    if value_type in {
        PredicateValueType.NUMBER,
        PredicateValueType.CURRENCY,
        PredicateValueType.PERCENTAGE,
    }:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == PredicateValueType.BOOLEAN:
        return isinstance(value, bool)
    if value_type == PredicateValueType.DATE:
        if not isinstance(value, str):
            return False
        try:
            date.fromisoformat(value[:10])
        except ValueError:
            return False
        return True
    return isinstance(value, str)


def _literal_matches_type(value: object, data_type: PlanDataType) -> bool:
    if data_type in _NUMERIC_TYPES:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if data_type == PlanDataType.BOOLEAN:
        return isinstance(value, bool)
    if data_type in {PlanDataType.DATE, PlanDataType.PERIOD}:
        return isinstance(value, str)
    if data_type == PlanDataType.STRING:
        return isinstance(value, str)
    return True


def _require_numeric_columns(
    schema: dict[str, PlanColumn],
    columns: Iterable[str],
    code: str,
    index: int,
    issues: list[PlanValidationIssue],
) -> None:
    if any(
        key in schema and schema[key].data_type not in _NUMERIC_TYPES
        for key in columns
    ):
        issues.append(
            _type_error(
                code,
                "The operation requires numeric feature columns.",
                index,
            )
        )


def _schema_map(columns: Iterable[PlanColumn]) -> dict[str, PlanColumn]:
    return {column.key: column for column in columns}


def _has_cycle(graph: dict[str, tuple[str, ...]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(dependency in graph and visit(dependency) for dependency in graph[node]):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def _error(
    code: str,
    layer: PlanValidationLayer,
    message: str,
    *,
    path: str = "",
    repairable: bool = True,
) -> PlanValidationIssue:
    return PlanValidationIssue(
        code=code,
        layer=layer,
        severity=PlanValidationSeverity.ERROR,
        message=message,
        path=path,
        repairable=repairable,
    )


def _type_error(
    code: str,
    message: str,
    index: int,
) -> PlanValidationIssue:
    return _error(
        code,
        PlanValidationLayer.TYPE_AND_UNIT,
        message,
        path=f"steps.{index}",
    )


def _resource_error(
    code: str,
    message: str,
    *,
    path: str = "",
    repairable: bool = True,
) -> PlanValidationIssue:
    return _error(
        code,
        PlanValidationLayer.RESOURCE,
        message,
        path=path,
        repairable=repairable,
    )


def _deduplicate_issues(
    issues: Iterable[PlanValidationIssue],
) -> tuple[PlanValidationIssue, ...]:
    output: list[PlanValidationIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (issue.code, issue.path, issue.message)
        if key not in seen:
            seen.add(key)
            output.append(issue)
    return tuple(output)


__all__ = ["AnalysisPlanValidator", "derive_approval_policy"]
