from __future__ import annotations

import unittest
from uuid import uuid4

from pydantic import ValidationError

from scripts.data_analysis_agent.runtime.models.capabilities import (
    CAPABILITY_PROFILE,
    ExecutorCapabilities,
)
from scripts.data_analysis_agent.runtime.models.expressions import (
    BinaryExpression,
    ColumnExpression,
    CompareExpression,
    CoalesceExpression,
    LiteralExpression,
)
from scripts.data_analysis_agent.runtime.models.generation import (
    DecimalRangeRule,
    GenerationColumn,
    GenerationNotNullConstraint,
    SequenceRule,
    SyntheticDatasetSpec,
    UniqueIdRule,
)
from scripts.data_analysis_agent.runtime.models.plans import (
    AnalysisPlan,
    ApprovalPolicy,
    GenerateDatasetStep,
    JoinKeyPair,
    JoinStep,
    LegacyFillMissingStep,
    LegacyFilterRowsStep,
    LegacyJoinStep,
    LegacyPivotStep,
    PlanApprovalCommand,
    PlanColumn,
    PlanDataType,
    PlanDiagnostics,
    PlanExecutor,
    PlanProposal,
    PlanStepEstimate,
    PivotCategoryPolicy,
    PivotStep,
    build_analysis_plan,
    compute_input_signature,
)
from scripts.data_analysis_agent.runtime.planning.service import (
    _draft as _service_draft,
    _validate_plan_decision,
)
from scripts.data_analysis_agent.runtime.planning.expression_validation import (
    validate_expression,
)
from scripts.data_analysis_agent.runtime.planning.validation import (
    AnalysisPlanValidator,
)
from scripts.data_analysis_agent.runtime.versioning import phase8_component_versions
from scripts.data_analysis_agent.runtime.repositories.plans import (
    AnalysisPlanConflictError,
)
from tests.test_data_analysis_phase8_planning import (
    _HASH_A,
    _approval_plan,
    _context,
    _proposal,
)


class Phase91CapabilityTests(unittest.TestCase):
    def test_capability_profile_is_honest_about_unavailable_engines(self) -> None:
        capabilities = ExecutorCapabilities()

        self.assertEqual(capabilities.capability_profile, CAPABILITY_PROFILE)
        self.assertTrue(capabilities.native_execution)
        self.assertFalse(capabilities.python_execution)
        self.assertFalse(capabilities.charts)
        self.assertFalse(capabilities.machine_learning)
        self.assertFalse(capabilities.native_execution_ready)
        self.assertEqual(capabilities.supported_plan_schema_versions, ("2.0",))
        self.assertNotIn("train_model", capabilities.supported_operations)

    def test_python_only_operations_fail_capability_validation(self) -> None:
        context = _context()
        proposal = _proposal(executor=PlanExecutor.PYTHON, with_write=False)

        report = AnalysisPlanValidator().validate(
            draft=_service_draft(context, proposal),
            context=context,
        )
        codes = {issue.code for issue in report.errors}

        self.assertIn("executor_unavailable", codes)
        self.assertIn("native_executor_required", codes)

    def test_capability_profile_rejects_unknown_native_operations(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unsupported operations"):
            ExecutorCapabilities(
                supported_operations=("filter_rows", "execute_arbitrary_code")
            )

    def test_legacy_plan_is_readable_but_cannot_be_approved(self) -> None:
        context = _context()
        current = _approval_plan(context)
        data = current.model_dump(mode="python")
        current_step = data["steps"][0]
        data["plan_version"] = "1.0"
        data.pop("capability_profile", None)
        data.pop("capability_version", None)
        data["plan_hash"] = _HASH_A
        data["steps"] = (
            {
                **{
                    key: value
                    for key, value in current_step.items()
                    if key not in {"predicate", "null_predicate_policy"}
                },
                "predicates": (
                    {
                        "kind": "comparison",
                        "column_key": "revenue",
                        "operator": "gt",
                        "value": 50_000,
                        "value_type": "currency",
                        "unit": "USD",
                    },
                ),
                "combine_with": "and",
            },
        )

        restored = AnalysisPlan.model_validate(data)

        self.assertEqual(restored.plan_version, "1.0")
        self.assertIsInstance(restored.steps[0], LegacyFilterRowsStep)
        with self.assertRaisesRegex(AnalysisPlanConflictError, "legacy plans"):
            _validate_plan_decision(
                restored,
                PlanApprovalCommand(
                    decision="approve",
                    plan_id=restored.plan_id,
                    expected_revision=restored.revision,
                    expected_plan_hash=restored.plan_hash,
                    expected_input_signature=restored.input_signature,
                    workbook_guards=context.workbook_guards,
                    decision_id=str(uuid4()),
                ),
            )

    def test_legacy_strengthened_steps_remain_readable_for_history(self) -> None:
        context = _context()
        current = _approval_plan(context)
        current_step = current.model_dump(mode="python")["steps"][0]
        shared = {
            key: current_step[key]
            for key in (
                "step_id",
                "depends_on",
                "executor",
                "output_alias",
                "expected_schema",
                "estimate",
                "assertions",
                "provenance",
                "network_access",
                "python_reason",
            )
        }
        legacy_steps = (
            (
                LegacyFillMissingStep,
                {
                    **shared,
                    "kind": "fill_missing",
                    "input_alias": "input_1",
                    "rules": (
                        {
                            "column_key": "revenue",
                            "strategy": "forward_fill",
                        },
                    ),
                },
            ),
            (
                LegacyJoinStep,
                {
                    **shared,
                    "kind": "join",
                    "left_alias": "input_1",
                    "right_alias": "input_1",
                    "join_type": "inner",
                    "keys": (
                        {
                            "left_column_key": "company",
                            "right_column_key": "company",
                        },
                    ),
                },
            ),
            (
                LegacyPivotStep,
                {
                    **shared,
                    "kind": "pivot",
                    "input_alias": "input_1",
                    "index_columns": ("company",),
                    "pivot_column": "company",
                    "value_column": "revenue",
                    "aggregation": "sum",
                },
            ),
        )

        for expected_type, step in legacy_steps:
            with self.subTest(step=expected_type.__name__):
                data = current.model_dump(mode="python")
                data["plan_version"] = "1.0"
                data.pop("capability_profile", None)
                data.pop("capability_version", None)
                data["plan_hash"] = _HASH_A
                data["steps"] = (step,)

                restored = AnalysisPlan.model_validate(data)

                self.assertIsInstance(restored.steps[0], expected_type)


class Phase92TypedContractTests(unittest.TestCase):
    def test_sequence_generation_rejects_a_zero_step(self) -> None:
        with self.assertRaisesRegex(ValidationError, "cannot be zero"):
            SequenceRule(step=0)

    def test_unknown_expression_operator_fails_closed(self) -> None:
        with self.assertRaises(ValidationError):
            BinaryExpression.model_validate(
                {
                    "kind": "binary",
                    "operator": "run_python",
                    "left": {
                        "kind": "column_ref",
                        "column_key": "revenue",
                    },
                    "right": {
                        "kind": "literal",
                        "value": 2,
                        "data_type": "integer",
                    },
                }
            )

    def test_safe_division_requires_an_explicit_zero_policy(self) -> None:
        operands = {
            "operator": "safe_divide",
            "left": {"kind": "column_ref", "column_key": "revenue"},
            "right": {"kind": "literal", "value": 2, "data_type": "integer"},
        }
        with self.assertRaisesRegex(ValidationError, "zero_division"):
            BinaryExpression.model_validate(operands)

        parsed = BinaryExpression.model_validate(
            {**operands, "zero_division": "error"}
        )

        self.assertEqual(parsed.zero_division, "error")

    def test_coalesce_nullability_uses_all_fallbacks(self) -> None:
        schema = {
            "required_value": PlanColumn(
                key="required_value",
                label="Required",
                data_type=PlanDataType.INTEGER,
                nullable=False,
            ),
            "optional_value": PlanColumn(
                key="optional_value",
                label="Optional",
                data_type=PlanDataType.INTEGER,
                nullable=True,
            ),
        }
        result, problems = validate_expression(
            CoalesceExpression(
                expressions=(
                    ColumnExpression(column_key="required_value"),
                    ColumnExpression(column_key="optional_value"),
                )
            ),
            schema=schema,
            path="expression",
        )

        self.assertFalse(problems)
        self.assertFalse(result.nullable)

    def test_filter_expression_must_be_boolean(self) -> None:
        context = _context()
        proposal = _proposal(with_write=False)
        step = proposal.steps[0].model_copy(
            update={
                "predicate": LiteralExpression(value=1, data_type="integer")
            }
        )
        report = AnalysisPlanValidator().validate(
            draft=_service_draft(
                context,
                proposal.model_copy(update={"steps": (step,)}),
            ),
            context=context,
        )

        self.assertIn(
            "filter_predicate_not_boolean",
            {issue.code for issue in report.errors},
        )

    def test_expression_units_are_checked_deterministically(self) -> None:
        context = _context()
        proposal = _proposal(with_write=False)
        step = proposal.steps[0].model_copy(
            update={
                "predicate": CompareExpression(
                    operator="greater_than",
                    left=ColumnExpression(column_key="revenue"),
                    right=LiteralExpression(
                        value=50_000,
                        data_type="decimal",
                        unit="EUR",
                    ),
                )
            }
        )
        report = AnalysisPlanValidator().validate(
            draft=_service_draft(
                context,
                proposal.model_copy(update={"steps": (step,)}),
            ),
            context=context,
        )

        self.assertIn(
            "expression_comparison_type_mismatch",
            {issue.code for issue in report.errors},
        )

    def test_seeded_generation_has_no_free_form_instructions(self) -> None:
        context = _context()
        columns = (
            PlanColumn(
                key="transaction_id",
                label="Transaction ID",
                data_type=PlanDataType.STRING,
                nullable=False,
            ),
            PlanColumn(
                key="revenue",
                label="Revenue",
                data_type=PlanDataType.CURRENCY,
                unit="USD",
                nullable=False,
            ),
        )
        generation = SyntheticDatasetSpec(
            dataset_name="sample_sales",
            row_count=100,
            seed=91_342,
            columns=(
                GenerationColumn(
                    column_key="transaction_id",
                    rule=UniqueIdRule(prefix="TX"),
                ),
                GenerationColumn(
                    column_key="revenue",
                    rule=DecimalRangeRule(
                        minimum_minor_units=100_000,
                        maximum_minor_units=10_000_000,
                        scale=2,
                    ),
                ),
            ),
        )
        step = GenerateDatasetStep(
            step_id="generate_sales",
            executor=PlanExecutor.NATIVE,
            output_alias="sample_sales",
            expected_schema=columns,
            generation=generation,
            estimate=PlanStepEstimate(output_rows=100),
        )
        draft = _service_draft(
            context,
            PlanProposal(intent="Generate sample sales.", steps=(step,)),
        )

        report = AnalysisPlanValidator().validate(draft=draft, context=context)

        self.assertTrue(report.valid, report.errors)
        serialized = draft.steps[0].model_dump(mode="json")
        self.assertEqual(serialized["generation"]["seed"], 91_342)
        self.assertNotIn("generation_instructions", serialized)
        self.assertNotIn("random_seed", serialized)

    def test_generation_rule_must_match_declared_column_type(self) -> None:
        context = _context()
        revenue = PlanColumn(
            key="revenue",
            label="Revenue",
            data_type=PlanDataType.CURRENCY,
            unit="USD",
        )
        step = GenerateDatasetStep(
            step_id="generate_invalid_sales",
            executor=PlanExecutor.NATIVE,
            output_alias="invalid_sales",
            expected_schema=(revenue,),
            generation=SyntheticDatasetSpec(
                dataset_name="invalid_sales",
                row_count=10,
                seed=7,
                columns=(
                    GenerationColumn(
                        column_key="revenue",
                        rule=UniqueIdRule(prefix="REV"),
                    ),
                ),
            ),
        )
        report = AnalysisPlanValidator().validate(
            draft=_service_draft(
                context,
                PlanProposal(intent="Generate invalid sales.", steps=(step,)),
            ),
            context=context,
        )

        self.assertIn(
            "generation_rule_type_mismatch",
            {issue.code for issue in report.errors},
        )

    def test_generation_null_contract_is_validated_deterministically(self) -> None:
        context = _context()
        sequence = PlanColumn(
            key="row_number",
            label="Row Number",
            data_type=PlanDataType.INTEGER,
            nullable=False,
        )
        step = GenerateDatasetStep(
            step_id="generate_rows",
            executor=PlanExecutor.NATIVE,
            output_alias="generated_rows",
            expected_schema=(sequence,),
            generation=SyntheticDatasetSpec(
                dataset_name="generated_rows",
                row_count=10,
                seed=9,
                columns=(
                    GenerationColumn(
                        column_key="row_number",
                        rule=SequenceRule(),
                        null_probability=0.1,
                    ),
                ),
                constraints=(
                    GenerationNotNullConstraint(column_keys=("row_number",)),
                ),
            ),
        )
        report = AnalysisPlanValidator().validate(
            draft=_service_draft(
                context,
                PlanProposal(intent="Generate rows.", steps=(step,)),
            ),
            context=context,
        )

        codes = {issue.code for issue in report.errors}
        self.assertIn("generation_nullability_mismatch", codes)
        self.assertIn("generation_not_null_conflict", codes)

    def test_join_output_schema_is_derived_from_the_suffix_policy(self) -> None:
        context = _context()
        source = context.input_datasets[0]
        step = JoinStep(
            step_id="join_company_rows",
            executor=PlanExecutor.NATIVE,
            output_alias="joined_rows",
            expected_schema=source.columns,
            left_alias="input_1",
            right_alias="input_1",
            join_type="inner",
            keys=(
                JoinKeyPair(
                    left_column_key="company",
                    right_column_key="company",
                ),
            ),
            expected_cardinality="one_to_one",
        )

        draft = _service_draft(
            context,
            PlanProposal(intent="Join matching companies.", steps=(step,)),
        )
        report = AnalysisPlanValidator().validate(draft=draft, context=context)

        self.assertTrue(report.valid, report.errors)
        self.assertEqual(
            tuple(column.key for column in draft.steps[0].expected_schema),
            ("company", "revenue_left", "revenue_right"),
        )

    def test_explicit_pivot_categories_must_match_the_category_type(self) -> None:
        base_context = _context()
        region = PlanColumn(
            key="region",
            label="Region",
            data_type=PlanDataType.STRING,
            nullable=False,
        )
        source = base_context.input_datasets[0].model_copy(
            update={
                "columns": (*base_context.input_datasets[0].columns, region),
            }
        )
        context = base_context.model_copy(
            update={
                "input_datasets": (source,),
                "input_signature": compute_input_signature((source,)),
            }
        )
        revenue = source.columns[1]
        result = revenue.model_copy(update={"key": "north_revenue"})
        step = PivotStep(
            step_id="pivot_revenue",
            executor=PlanExecutor.NATIVE,
            output_alias="pivoted_revenue",
            input_alias="input_1",
            index_columns=("company",),
            pivot_column="region",
            value_column="revenue",
            aggregation="sum",
            category_policy=PivotCategoryPolicy(
                mode="explicit",
                values=(123,),
            ),
            expected_schema=(source.columns[0], result),
        )
        report = AnalysisPlanValidator().validate(
            draft=_service_draft(
                context,
                PlanProposal(intent="Pivot revenue by region.", steps=(step,)),
            ),
            context=context,
        )

        self.assertIn(
            "pivot_category_type_mismatch",
            {issue.code for issue in report.errors},
        )

    def test_plan_hash_ignores_display_text_but_binds_execution_semantics(self) -> None:
        context = _context()
        original_draft = _service_draft(context, _proposal(with_write=False))
        policy = ApprovalPolicy(
            plan_approval_required=False,
            final_patch_approval_required=False,
            auto_execute_read_only=True,
        )

        input_dataset = original_draft.input_datasets[0]
        relabelled_input = input_dataset.model_copy(
            update={
                "title": "Cosmetic dataset title",
                "columns": tuple(
                    column.model_copy(update={"label": f"Display {column.key}"})
                    for column in input_dataset.columns
                ),
            }
        )
        original_step = original_draft.steps[0]
        relabelled_step = original_step.model_copy(
            update={
                "expected_schema": tuple(
                    column.model_copy(update={"label": f"Output {column.key}"})
                    for column in original_step.expected_schema
                ),
                "provenance": original_step.provenance.model_copy(
                    update={"description": "Cosmetic lineage description."}
                ),
            }
        )
        relabelled_draft = original_draft.model_copy(
            update={
                "intent": "Different display intent.",
                "assumptions": ("Different display assumption.",),
                "input_datasets": (relabelled_input,),
                "steps": (relabelled_step,),
            }
        )
        relabelled_draft = relabelled_draft.model_copy(
            update={
                "input_signature": compute_input_signature(
                    relabelled_draft.input_datasets
                )
            }
        )

        def build(draft):
            return build_analysis_plan(
                draft=draft,
                user_id=context.user_id,
                workspace_id=context.workspace_id,
                revision=1,
                approval_policy=policy,
                diagnostics=PlanDiagnostics(
                    generation_attempt=1,
                    repair_count=0,
                ),
                model="test-planner",
            )

        original = build(original_draft)
        relabelled = build(relabelled_draft)
        changed_predicate = original_step.predicate.model_copy(
            update={
                "right": original_step.predicate.right.model_copy(
                    update={"value": 60_000}
                )
            }
        )
        changed = build(
            original_draft.model_copy(
                update={
                    "steps": (
                        original_step.model_copy(
                            update={"predicate": changed_predicate}
                        ),
                    )
                }
            )
        )

        self.assertEqual(original.plan_hash, relabelled.plan_hash)
        self.assertEqual(
            original_draft.input_signature,
            relabelled_draft.input_signature,
        )
        self.assertNotEqual(original.plan_hash, changed.plan_hash)

    def test_component_versions_include_phase9_contracts(self) -> None:
        versions = phase8_component_versions()

        self.assertEqual(versions["plan_schema"], "2.0")
        self.assertIn("plan_canonicalizer", versions)
        self.assertEqual(versions["capability_profile"], CAPABILITY_PROFILE)
        self.assertIn("synthetic_generator", versions)


if __name__ == "__main__":
    unittest.main()
