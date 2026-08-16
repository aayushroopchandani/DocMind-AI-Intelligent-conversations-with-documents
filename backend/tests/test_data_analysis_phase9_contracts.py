"""Regression cover for the Phase 9.1/9.2 contract repairs.

Each test here pins a defect that was found by reviewing the Phase 9 commits
against the surrounding Phase 1-8 pipeline:

* canonical plan hashing stripped display *keys* from opaque user payloads;
* the execution queue had no consumer and no readiness gate;
* two literal/compatibility type systems disagreed with each other;
* legacy v1 steps were silently upgraded into the v2 union;
* selective early approval (9.1.3) was never implemented.
"""

from __future__ import annotations

import unittest
from datetime import date

from pydantic import TypeAdapter, ValidationError

from scripts.data_analysis_agent.runtime.execution.admission import (
    ExecutionAdmission,
    evaluate_admission,
    plan_contract_mismatch,
)
from scripts.data_analysis_agent.runtime.models.canonical import (
    CanonicalContentError,
    canonical_content,
    undeclared_display_only_fields,
)
from scripts.data_analysis_agent.runtime.models.capabilities import (
    ExecutorCapabilities,
)
from scripts.data_analysis_agent.runtime.models.expressions import (
    BinaryExpression,
    ColumnExpression,
    CompareExpression,
    LiteralExpression,
)
from scripts.data_analysis_agent.runtime.models.generation import (
    CategoricalRule,
    ConstantRule,
    GenerationColumn,
    SyntheticDatasetSpec,
)
from scripts.data_analysis_agent.runtime.models.plans import (
    PLAN_CANONICALIZER_VERSION,
    ApprovalPolicy,
    ApprovalReason,
    ColumnRename,
    ComparisonOperator,
    ComparisonPredicate,
    ExpectedArtifact,
    FilterRowsStep,
    GenerateDatasetStep,
    HistoricalPlanStep,
    LegacyDeriveColumnStep,
    LegacyFilterRowsStep,
    LegacyGenerateDatasetStep,
    LegacyJoinStep,
    LegacyPivotStep,
    PlanColumn,
    PlanDataType,
    PlanDiagnostics,
    PlanExecutor,
    PlanInputDataset,
    PlanStepEstimate,
    PredicateValueType,
    StepProvenance,
    build_analysis_plan,
    step_input_aliases,
)
from scripts.data_analysis_agent.runtime.planning.type_system import (
    literal_matches,
    types_compatible,
    wider_numeric_type,
)
from scripts.data_analysis_agent.runtime.planning.validation import (
    AnalysisPlanValidator,
    derive_approval_policy,
)
from scripts.data_analysis_agent.runtime.planning.expression_validation import (
    validate_expression,
)

from tests.test_data_analysis_phase8_planning import (
    _context,
    _proposal,
    _service_draft,
)


def _plan(context, proposal, *, revision: int = 1):
    return build_analysis_plan(
        draft=_service_draft(context, proposal),
        user_id=context.user_id,
        workspace_id=context.workspace_id,
        revision=revision,
        approval_policy=ApprovalPolicy(
            plan_approval_required=False,
            final_patch_approval_required=False,
            auto_execute_read_only=True,
        ),
        diagnostics=PlanDiagnostics(generation_attempt=1, repair_count=0),
        model="test-planner",
    )


class CanonicalPlanHashTests(unittest.TestCase):
    """The hash must separate display fields from opaque user payloads."""

    def test_opaque_payloads_keep_keys_that_name_a_display_field(self) -> None:
        def spec(values: tuple[object, ...]) -> SyntheticDatasetSpec:
            return SyntheticDatasetSpec(
                dataset_name="regions",
                row_count=3,
                seed=7,
                columns=(
                    GenerationColumn(
                        column_key="region",
                        rule=CategoricalRule(values=values),
                    ),
                ),
            )

        north = canonical_content(spec(({"title": "North"}, {"title": "South"})))
        east = canonical_content(spec(({"title": "East"}, {"title": "West"})))

        self.assertEqual(
            north["columns"][0]["rule"]["values"],
            [{"title": "North"}, {"title": "South"}],
        )
        self.assertNotEqual(north, east)

    def test_nested_literal_objects_survive_canonicalization(self) -> None:
        first = canonical_content(ConstantRule(value={"label": "a", "description": 1}))
        second = canonical_content(ConstantRule(value={"label": "b", "description": 2}))

        self.assertEqual(first["value"], {"label": "a", "description": 1})
        self.assertNotEqual(first, second)

    def test_declared_display_fields_are_dropped_by_field_identity(self) -> None:
        column = PlanColumn(
            key="revenue",
            label="Revenue shown to the user",
            data_type=PlanDataType.CURRENCY,
            unit="USD",
        )

        canonical = canonical_content(column)

        self.assertNotIn("label", canonical)
        self.assertEqual(canonical["key"], "revenue")
        self.assertEqual(canonical["unit"], "USD")

    def test_every_display_declaration_names_a_real_field(self) -> None:
        for model in (
            PlanColumn,
            PlanInputDataset,
            StepProvenance,
            ColumnRename,
            ExpectedArtifact,
        ):
            with self.subTest(model=model.__name__):
                self.assertEqual(undeclared_display_only_fields(model), frozenset())

    def test_canonical_content_fails_closed_on_unhashable_values(self) -> None:
        with self.assertRaises(CanonicalContentError):
            canonical_content({"key": object()})

    def test_dates_and_enums_are_json_stable(self) -> None:
        self.assertEqual(canonical_content(date(2026, 8, 16)), "2026-08-16")
        self.assertEqual(canonical_content(PlanDataType.CURRENCY), "currency")

    def test_display_text_still_does_not_change_the_plan_hash(self) -> None:
        context = _context()
        proposal = _proposal(with_write=False)
        original = _plan(context, proposal)
        relabelled_step = proposal.steps[0].model_copy(
            update={
                "provenance": proposal.steps[0].provenance.model_copy(
                    update={"description": "Cosmetic lineage wording."}
                )
            }
        )
        relabelled = _plan(
            context,
            proposal.model_copy(
                update={
                    "intent": "Completely different wording.",
                    "steps": (relabelled_step,),
                }
            ),
        )

        self.assertEqual(original.plan_hash, relabelled.plan_hash)


class CanonicalizerVersioningTests(unittest.TestCase):
    """Bumping canonicalization must not make stored plans unreadable."""

    def test_plan_written_by_an_older_canonicalizer_stays_readable(self) -> None:
        context = _context()
        plan = _plan(context, _proposal(with_write=False))
        stored = plan.model_dump(mode="python")
        stored["canonicalizer_version"] = "2.0.0"

        restored = type(plan).model_validate(stored)

        self.assertEqual(restored.canonicalizer_version, "2.0.0")
        self.assertEqual(restored.plan_hash, plan.plan_hash)

    def test_an_older_canonicalizer_is_not_executable(self) -> None:
        context = _context()
        plan = _plan(context, _proposal(with_write=False))
        stale = type(plan).model_validate(
            {**plan.model_dump(mode="python"), "canonicalizer_version": "2.0.0"}
        )

        self.assertEqual(plan_contract_mismatch(stale), "canonicalizer_version")
        self.assertTrue(evaluate_admission(stale).rejected)

    def test_a_current_plan_passes_the_contract_check(self) -> None:
        context = _context()
        plan = _plan(context, _proposal(with_write=False))

        self.assertEqual(plan.canonicalizer_version, PLAN_CANONICALIZER_VERSION)
        self.assertIsNone(plan_contract_mismatch(plan))


class ExecutionAdmissionTests(unittest.TestCase):
    """9.1.2 must not park runs in a queue that nothing drains."""

    def test_a_valid_plan_only_plans_while_no_engine_is_installed(self) -> None:
        context = _context()
        plan = _plan(context, _proposal(with_write=False))

        decision = evaluate_admission(plan, ExecutorCapabilities())

        self.assertIs(decision.admission, ExecutionAdmission.PLAN_ONLY)
        self.assertFalse(decision.queued)
        self.assertFalse(decision.rejected)

    def test_a_valid_plan_queues_once_the_engine_is_installed(self) -> None:
        context = _context()
        plan = _plan(context, _proposal(with_write=False))

        decision = evaluate_admission(
            plan,
            ExecutorCapabilities(native_execution_ready=True),
        )

        self.assertIs(decision.admission, ExecutionAdmission.QUEUE)
        self.assertTrue(decision.queued)

    def test_readiness_cannot_be_declared_without_native_execution(self) -> None:
        with self.assertRaisesRegex(ValueError, "native execution readiness"):
            ExecutorCapabilities(
                native_execution=False,
                native_execution_ready=True,
            )

    def test_a_legacy_plan_is_rejected_regardless_of_engine_readiness(self) -> None:
        context = _context()
        plan = _plan(context, _proposal(with_write=False))
        legacy = plan.model_copy(update={"plan_version": "1.0"})

        for capabilities in (
            ExecutorCapabilities(),
            ExecutorCapabilities(native_execution_ready=True),
        ):
            with self.subTest(ready=capabilities.native_execution_ready):
                self.assertTrue(evaluate_admission(legacy, capabilities).rejected)


class SharedTypeSystemTests(unittest.TestCase):
    """One question, one answer, across both validation layers."""

    def test_integer_columns_reject_fractional_literals(self) -> None:
        self.assertFalse(literal_matches(1.5, PlanDataType.INTEGER))
        self.assertTrue(literal_matches(2, PlanDataType.INTEGER))
        self.assertFalse(literal_matches(True, PlanDataType.INTEGER))

    def test_date_columns_reject_a_string_that_is_not_a_date(self) -> None:
        self.assertFalse(literal_matches("not-a-date", PlanDataType.DATE))
        self.assertTrue(literal_matches("2026-08-16", PlanDataType.DATE))
        self.assertTrue(literal_matches(date(2026, 8, 16), PlanDataType.DATE))

    def test_currency_and_percentage_only_match_themselves(self) -> None:
        self.assertFalse(
            types_compatible(
                PlanDataType.CURRENCY,
                "USD",
                PlanDataType.PERCENTAGE,
                "USD",
            )
        )
        self.assertFalse(
            types_compatible(
                PlanDataType.CURRENCY,
                "USD",
                PlanDataType.DECIMAL,
                "USD",
            )
        )
        self.assertTrue(
            types_compatible(
                PlanDataType.CURRENCY,
                "USD",
                PlanDataType.CURRENCY,
                "USD",
            )
        )

    def test_units_must_match_exactly(self) -> None:
        self.assertFalse(
            types_compatible(
                PlanDataType.CURRENCY,
                "USD",
                PlanDataType.CURRENCY,
                "EUR",
            )
        )

    def test_arithmetic_preserves_a_semantic_numeric_type(self) -> None:
        self.assertEqual(
            wider_numeric_type(PlanDataType.CURRENCY, PlanDataType.CURRENCY),
            PlanDataType.CURRENCY,
        )
        self.assertEqual(
            wider_numeric_type(PlanDataType.CURRENCY, PlanDataType.NUMBER),
            PlanDataType.CURRENCY,
        )
        self.assertEqual(
            wider_numeric_type(PlanDataType.CURRENCY, PlanDataType.PERCENTAGE),
            PlanDataType.UNKNOWN,
        )

    def test_generation_rules_use_the_same_strict_literal_rules(self) -> None:
        context = _context()
        proposal = _proposal(with_write=False)
        step = GenerateDatasetStep(
            step_id="generate_dates",
            executor=PlanExecutor.NATIVE,
            output_alias="generated",
            expected_schema=(
                PlanColumn(
                    key="captured_on",
                    label="Captured on",
                    data_type=PlanDataType.DATE,
                ),
            ),
            estimate=PlanStepEstimate(rows_scanned=0, duration_seconds=1),
            provenance=StepProvenance(
                generated=True,
                description="Deterministically generated rows.",
            ),
            generation=SyntheticDatasetSpec(
                dataset_name="dates",
                row_count=3,
                seed=1,
                columns=(
                    GenerationColumn(
                        column_key="captured_on",
                        rule=ConstantRule(value="definitely not a date"),
                    ),
                ),
            ),
        )

        report = AnalysisPlanValidator().validate(
            draft=_service_draft(
                context,
                proposal.model_copy(update={"steps": (step,)}),
            ),
            context=context,
        )

        self.assertIn(
            "generation_rule_type_mismatch",
            {issue.code for issue in report.errors},
        )

    def test_a_currency_column_accepts_a_currency_literal(self) -> None:
        schema = {
            "revenue": PlanColumn(
                key="revenue",
                label="Revenue",
                data_type=PlanDataType.CURRENCY,
                unit="USD",
            )
        }
        result, problems = validate_expression(
            CompareExpression(
                operator="greater_than",
                left=ColumnExpression(column_key="revenue"),
                right=LiteralExpression(
                    value=50_000,
                    data_type="currency",
                    unit="USD",
                ),
            ),
            schema=schema,
            path="predicate",
        )

        self.assertEqual(problems, ())
        self.assertEqual(result.data_type, PlanDataType.BOOLEAN)

    def test_a_currency_column_rejects_a_plain_decimal_literal(self) -> None:
        schema = {
            "revenue": PlanColumn(
                key="revenue",
                label="Revenue",
                data_type=PlanDataType.CURRENCY,
                unit="USD",
            )
        }
        _result, problems = validate_expression(
            CompareExpression(
                operator="greater_than",
                left=ColumnExpression(column_key="revenue"),
                right=LiteralExpression(
                    value=50_000,
                    data_type="decimal",
                    unit="USD",
                ),
            ),
            schema=schema,
            path="predicate",
        )

        self.assertIn(
            "expression_comparison_type_mismatch",
            {problem.code for problem in problems},
        )

    def test_currency_subtraction_still_yields_currency(self) -> None:
        schema = {
            key: PlanColumn(
                key=key,
                label=key.title(),
                data_type=PlanDataType.CURRENCY,
                unit="USD",
                nullable=False,
            )
            for key in ("revenue", "cost")
        }
        result, problems = validate_expression(
            BinaryExpression(
                operator="subtract",
                left=ColumnExpression(column_key="revenue"),
                right=ColumnExpression(column_key="cost"),
            ),
            schema=schema,
            path="expression",
        )

        self.assertEqual(problems, ())
        self.assertEqual(result.data_type, PlanDataType.CURRENCY)
        self.assertEqual(result.unit, "USD")


class LegacyStepResolutionTests(unittest.TestCase):
    """v1 shapes must resolve to the legacy models, never to v2 ones."""

    def _legacy_base(self, kind: str) -> dict[str, object]:
        return {
            "kind": kind,
            "step_id": "legacy_step",
            "executor": "native",
            "output_alias": "legacy_out",
            "expected_schema": [
                {
                    "key": "amount",
                    "label": "Amount",
                    "data_type": "number",
                    "nullable": False,
                }
            ],
        }

    def test_a_v1_filter_never_becomes_a_v2_filter(self) -> None:
        payload = {
            **self._legacy_base("filter_rows"),
            "input_alias": "src",
            "predicates": [
                {
                    "kind": "comparison",
                    "column_key": "amount",
                    "operator": "gt",
                    "value": 10,
                    "value_type": "number",
                }
            ],
            "combine_with": "and",
        }

        resolved = TypeAdapter(HistoricalPlanStep).validate_python(payload)

        self.assertIsInstance(resolved, LegacyFilterRowsStep)

    def test_every_legacy_variant_resolves_to_its_own_model(self) -> None:
        cases = (
            (
                {
                    **self._legacy_base("generate_dataset"),
                    "row_count": 5,
                    "generation_instructions": "make five rows",
                },
                LegacyGenerateDatasetStep,
            ),
            (
                {
                    **self._legacy_base("derive_column"),
                    "input_alias": "src",
                    "output_column": {
                        "key": "margin",
                        "label": "Margin",
                        "data_type": "number",
                        "nullable": True,
                    },
                    "expression": "amount * 2",
                    "referenced_columns": ["amount"],
                    "expression_language": "native",
                },
                LegacyDeriveColumnStep,
            ),
            (
                {
                    **self._legacy_base("join"),
                    "left_alias": "a",
                    "right_alias": "b",
                    "join_type": "inner",
                    "keys": [
                        {"left_column_key": "amount", "right_column_key": "amount"}
                    ],
                },
                LegacyJoinStep,
            ),
            (
                {
                    **self._legacy_base("pivot"),
                    "input_alias": "src",
                    "index_columns": ["amount"],
                    "pivot_column": "quarter",
                    "value_column": "total",
                    "aggregation": "sum",
                },
                LegacyPivotStep,
            ),
        )
        for payload, expected in cases:
            with self.subTest(kind=payload["kind"]):
                resolved = TypeAdapter(HistoricalPlanStep).validate_python(payload)
                self.assertIsInstance(resolved, expected)

    def test_a_v2_filter_step_refuses_a_flat_predicate_list(self) -> None:
        """The v1 shape must fail closed rather than be silently upgraded."""

        payload = {
            **self._legacy_base("filter_rows"),
            "input_alias": "src",
            "predicates": [
                ComparisonPredicate(
                    column_key="amount",
                    operator=ComparisonOperator.GT,
                    value=10,
                    value_type=PredicateValueType.NUMBER,
                ).model_dump(mode="json")
            ],
        }

        with self.assertRaises(ValidationError):
            FilterRowsStep.model_validate(payload)

    def test_step_input_aliases_handles_persisted_legacy_steps(self) -> None:
        generate = TypeAdapter(HistoricalPlanStep).validate_python(
            {
                **self._legacy_base("generate_dataset"),
                "row_count": 5,
                "generation_instructions": "make five rows",
            }
        )
        join = TypeAdapter(HistoricalPlanStep).validate_python(
            {
                **self._legacy_base("join"),
                "left_alias": "a",
                "right_alias": "b",
                "join_type": "inner",
                "keys": [{"left_column_key": "amount", "right_column_key": "amount"}],
            }
        )

        self.assertEqual(step_input_aliases(generate), ())
        self.assertEqual(step_input_aliases(join), ("a", "b"))


class SelectiveApprovalPolicyTests(unittest.TestCase):
    """9.1.3: gate what matters, and only what matters."""

    def test_a_low_risk_read_only_plan_executes_without_early_approval(self) -> None:
        context = _context()
        draft = _service_draft(context, _proposal(with_write=False))

        policy = derive_approval_policy(draft=draft, context=context)

        self.assertFalse(policy.plan_approval_required)
        self.assertEqual(policy.plan_approval_reasons, ())
        self.assertTrue(policy.auto_execute_read_only)
        self.assertFalse(policy.final_patch_approval_required)

    def test_a_cheap_edit_still_needs_only_the_final_patch_approval(self) -> None:
        context = _context()
        draft = _service_draft(context, _proposal())

        policy = derive_approval_policy(draft=draft, context=context)

        self.assertFalse(policy.plan_approval_required)
        self.assertTrue(policy.final_patch_approval_required)

    def test_a_destructive_write_is_gated_early(self) -> None:
        context = _context()
        draft = _service_draft(context, _proposal(destructive=True))

        policy = derive_approval_policy(draft=draft, context=context)

        self.assertTrue(policy.plan_approval_required)
        self.assertIn(ApprovalReason.DESTRUCTIVE_WRITE, policy.plan_approval_reasons)

    def test_a_formula_overwrite_is_gated_early(self) -> None:
        context = _context()
        draft = _service_draft(context, _proposal(overwrite_formulas=True))

        policy = derive_approval_policy(draft=draft, context=context)

        self.assertIn(ApprovalReason.FORMULA_OVERWRITE, policy.plan_approval_reasons)

    def test_a_broad_workbook_write_is_gated_early(self) -> None:
        context = _context()
        draft = _service_draft(context, _proposal())
        narrow = derive_approval_policy(draft=draft, context=context)
        tight_context = context.model_copy(
            update={
                "resource_policy": context.resource_policy.model_copy(
                    update={"plan_approval_cells_written": 1}
                )
            }
        )

        broad = derive_approval_policy(draft=draft, context=tight_context)

        self.assertNotIn(ApprovalReason.BROAD_IMPACT, narrow.plan_approval_reasons)
        self.assertIn(ApprovalReason.BROAD_IMPACT, broad.plan_approval_reasons)
        self.assertTrue(broad.plan_approval_required)

    def test_an_assumption_heavy_plan_is_treated_as_ambiguous(self) -> None:
        context = _context()
        proposal = _proposal(with_write=False).model_copy(
            update={
                "assumptions": (
                    "Revenue is reported in USD.",
                    "Blank rows are inactive customers.",
                    "The fiscal year starts in January.",
                )
            }
        )
        draft = _service_draft(context, proposal)

        policy = derive_approval_policy(draft=draft, context=context)

        self.assertIn(ApprovalReason.AMBIGUOUS_REQUEST, policy.plan_approval_reasons)
        self.assertTrue(policy.plan_approval_required)

    def test_approval_reasons_are_deterministic(self) -> None:
        context = _context()
        draft = _service_draft(
            context,
            _proposal(destructive=True, overwrite_formulas=True),
        )

        first = derive_approval_policy(draft=draft, context=context)
        second = derive_approval_policy(draft=draft, context=context)

        self.assertEqual(first.plan_approval_reasons, second.plan_approval_reasons)
        self.assertEqual(
            len(set(first.plan_approval_reasons)),
            len(first.plan_approval_reasons),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
