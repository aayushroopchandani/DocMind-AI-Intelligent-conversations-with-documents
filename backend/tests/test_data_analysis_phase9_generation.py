"""Phase 9.6 seeded synthetic-data generation.

The acceptance criteria these cover:

* the same schema, seed and version produce the same content hash;
* column reordering does not change generated values;
* revenue/cost constraints always hold;
* limits are enforced before anything is generated.
"""

from __future__ import annotations

import unittest
from datetime import date

from scripts.data_analysis_agent.runtime.execution.native.generation import (
    GenerationError,
    GenerationLimits,
    column_seed,
    generate_dataset,
)
from scripts.data_analysis_agent.runtime.models.generation import (
    BooleanRule,
    CategoricalRule,
    ConstantRule,
    DateRangeRule,
    DecimalRangeRule,
    DependentFractionRule,
    GenerationColumn,
    GenerationComparisonConstraint,
    GenerationNotNullConstraint,
    GenerationUniqueConstraint,
    IntegerRangeRule,
    SequenceRule,
    SyntheticDatasetSpec,
    UniqueIdRule,
)
from scripts.data_analysis_agent.runtime.models.plans import (
    PlanColumn,
    PlanDataType,
)


def column(key: str, data_type: PlanDataType, *, unit=None):
    return PlanColumn(
        key=key,
        label=key.replace("_", " ").title(),
        data_type=data_type,
        unit=unit,
    )


class SalesFixture:
    """The worked example from the Phase 9.6 plan document."""

    plan_columns = (
        column("transaction_id", PlanDataType.STRING),
        column("region", PlanDataType.STRING),
        column("revenue", PlanDataType.CURRENCY, unit="USD"),
        column("cost", PlanDataType.CURRENCY, unit="USD"),
    )

    generation_columns = (
        GenerationColumn(
            column_key="transaction_id",
            rule=UniqueIdRule(prefix="TX", width=6),
        ),
        GenerationColumn(
            column_key="region",
            rule=CategoricalRule(values=("North", "South", "East", "West")),
        ),
        GenerationColumn(
            column_key="revenue",
            rule=DecimalRangeRule(
                minimum_minor_units=100_000,
                maximum_minor_units=10_000_000,
                scale=2,
            ),
        ),
        GenerationColumn(
            column_key="cost",
            rule=DependentFractionRule(
                source_column_key="revenue",
                minimum_fraction=0.35,
                maximum_fraction=0.85,
                scale=2,
            ),
        ),
    )

    @classmethod
    def spec(cls, **overrides) -> SyntheticDatasetSpec:
        values = {
            "dataset_name": "sample_sales",
            "row_count": 100,
            "seed": 91_342,
            "columns": cls.generation_columns,
            "constraints": (
                GenerationUniqueConstraint(column_keys=("transaction_id",)),
                GenerationComparisonConstraint(
                    left_column_key="cost",
                    operator="less_than",
                    right_column_key="revenue",
                ),
            ),
        }
        values.update(overrides)
        return SyntheticDatasetSpec(**values)


class DeterminismTests(unittest.TestCase):
    def test_the_same_spec_and_seed_produce_the_same_table(self) -> None:
        spec = SalesFixture.spec()

        first = generate_dataset(spec, columns=SalesFixture.plan_columns)
        second = generate_dataset(spec, columns=SalesFixture.plan_columns)

        self.assertTrue(first.equals(second))

    def test_a_different_seed_produces_different_values(self) -> None:
        baseline = generate_dataset(
            SalesFixture.spec(),
            columns=SalesFixture.plan_columns,
        )
        reseeded = generate_dataset(
            SalesFixture.spec(seed=91_343),
            columns=SalesFixture.plan_columns,
        )

        self.assertNotEqual(
            baseline.get_column("revenue").to_list(),
            reseeded.get_column("revenue").to_list(),
        )

    def test_reordering_columns_does_not_change_their_values(self) -> None:
        baseline = generate_dataset(
            SalesFixture.spec(),
            columns=SalesFixture.plan_columns,
        )
        shuffled = SalesFixture.generation_columns
        reordered = generate_dataset(
            SalesFixture.spec(
                columns=(shuffled[1], shuffled[0], shuffled[2], shuffled[3])
            ),
            columns=(
                SalesFixture.plan_columns[1],
                SalesFixture.plan_columns[0],
                SalesFixture.plan_columns[2],
                SalesFixture.plan_columns[3],
            ),
        )

        # This is what per-column seeding buys: position is not an input.
        self.assertEqual(
            baseline.get_column("region").to_list(),
            reordered.get_column("region").to_list(),
        )
        self.assertEqual(
            baseline.get_column("revenue").to_list(),
            reordered.get_column("revenue").to_list(),
        )

    def test_adding_a_column_leaves_the_others_untouched(self) -> None:
        baseline = generate_dataset(
            SalesFixture.spec(),
            columns=SalesFixture.plan_columns,
        )
        widened = generate_dataset(
            SalesFixture.spec(
                columns=(
                    *SalesFixture.generation_columns,
                    GenerationColumn(column_key="flagged", rule=BooleanRule()),
                )
            ),
            columns=(
                *SalesFixture.plan_columns,
                column("flagged", PlanDataType.BOOLEAN),
            ),
        )

        self.assertEqual(
            baseline.get_column("revenue").to_list(),
            widened.get_column("revenue").to_list(),
        )

    def test_column_seeds_differ_per_column_and_per_global_seed(self) -> None:
        first = column_seed(
            global_seed=1,
            generator_version="1.0",
            column_key="revenue",
        )
        other_column = column_seed(
            global_seed=1,
            generator_version="1.0",
            column_key="cost",
        )
        other_global = column_seed(
            global_seed=2,
            generator_version="1.0",
            column_key="revenue",
        )

        self.assertNotEqual(first, other_column)
        self.assertNotEqual(first, other_global)


class ConstraintTests(unittest.TestCase):
    def test_the_comparison_constraint_always_holds(self) -> None:
        frame = generate_dataset(
            SalesFixture.spec(),
            columns=SalesFixture.plan_columns,
        )

        cheaper = frame.get_column("cost") < frame.get_column("revenue")
        self.assertTrue(cheaper.all())

    def test_generated_identifiers_are_unique(self) -> None:
        frame = generate_dataset(
            SalesFixture.spec(),
            columns=SalesFixture.plan_columns,
        )

        self.assertEqual(
            frame.get_column("transaction_id").n_unique(),
            frame.height,
        )

    def test_an_unsatisfiable_constraint_fails_instead_of_looping(self) -> None:
        # cost is always a fraction of revenue, so it can never exceed it.
        spec = SalesFixture.spec(
            constraints=(
                GenerationComparisonConstraint(
                    left_column_key="cost",
                    operator="greater_than",
                    right_column_key="revenue",
                ),
            )
        )

        with self.assertRaises(GenerationError) as caught:
            generate_dataset(spec, columns=SalesFixture.plan_columns)

        self.assertIn("attempts", str(caught.exception))

    def test_a_not_null_constraint_is_enforced(self) -> None:
        spec = SalesFixture.spec(
            columns=(
                GenerationColumn(
                    column_key="transaction_id",
                    rule=UniqueIdRule(prefix="TX", width=6),
                ),
            ),
            constraints=(
                GenerationNotNullConstraint(column_keys=("transaction_id",)),
            ),
        )

        frame = generate_dataset(
            spec,
            columns=(SalesFixture.plan_columns[0],),
        )

        self.assertEqual(frame.get_column("transaction_id").null_count(), 0)


class RuleTests(unittest.TestCase):
    def _single(self, key, rule, plan_column, *, row_count=20, **spec_kwargs):
        spec = SyntheticDatasetSpec(
            dataset_name="rule_probe",
            row_count=row_count,
            seed=7,
            columns=(GenerationColumn(column_key=key, rule=rule, **spec_kwargs),),
        )
        return generate_dataset(spec, columns=(plan_column,))

    def test_a_sequence_counts_from_its_declared_start(self) -> None:
        frame = self._single(
            "row_number",
            SequenceRule(start=10, step=5),
            column("row_number", PlanDataType.INTEGER),
            row_count=3,
        )

        self.assertEqual(frame.get_column("row_number").to_list(), [10, 15, 20])

    def test_an_integer_range_includes_both_bounds(self) -> None:
        frame = self._single(
            "score",
            IntegerRangeRule(minimum=1, maximum=3),
            column("score", PlanDataType.INTEGER),
            row_count=200,
        )

        values = set(frame.get_column("score").to_list())
        self.assertTrue(values.issubset({1, 2, 3}))
        self.assertEqual(values, {1, 2, 3})

    def test_money_keeps_its_declared_scale(self) -> None:
        frame = self._single(
            "amount",
            DecimalRangeRule(
                minimum_minor_units=1,
                maximum_minor_units=999_999,
                scale=2,
            ),
            column("amount", PlanDataType.CURRENCY, unit="USD"),
        )

        for value in frame.get_column("amount").to_list():
            self.assertEqual(round(value, 2), value)

    def test_dates_stay_inside_the_declared_range(self) -> None:
        frame = self._single(
            "captured_on",
            DateRangeRule(start=date(2026, 1, 1), end=date(2026, 1, 10)),
            column("captured_on", PlanDataType.DATE),
        )

        for value in frame.get_column("captured_on").to_list():
            self.assertGreaterEqual(value, date(2026, 1, 1))
            self.assertLessEqual(value, date(2026, 1, 10))

    def test_a_constant_fills_every_row(self) -> None:
        frame = self._single(
            "currency",
            ConstantRule(value="USD"),
            column("currency", PlanDataType.STRING),
            row_count=5,
        )

        self.assertEqual(frame.get_column("currency").to_list(), ["USD"] * 5)

    def test_null_probability_blanks_a_deterministic_subset(self) -> None:
        first = self._single(
            "score",
            IntegerRangeRule(minimum=1, maximum=100),
            column("score", PlanDataType.INTEGER),
            row_count=200,
            null_probability=0.3,
        )
        second = self._single(
            "score",
            IntegerRangeRule(minimum=1, maximum=100),
            column("score", PlanDataType.INTEGER),
            row_count=200,
            null_probability=0.3,
        )

        self.assertGreater(first.get_column("score").null_count(), 0)
        self.assertEqual(
            first.get_column("score").to_list(),
            second.get_column("score").to_list(),
        )

    def test_weighted_categories_respect_their_weights(self) -> None:
        frame = self._single(
            "tier",
            CategoricalRule(values=("common", "rare"), weights=(0.95, 0.05)),
            column("tier", PlanDataType.STRING),
            row_count=1_000,
        )

        counts = frame.get_column("tier").value_counts()
        common = counts.filter(counts["tier"] == "common")["count"].item()
        self.assertGreater(common, 850)


class LimitTests(unittest.TestCase):
    def test_too_many_rows_fails_before_generating(self) -> None:
        spec = SalesFixture.spec(row_count=5_000)

        with self.assertRaises(GenerationError) as caught:
            generate_dataset(
                spec,
                columns=SalesFixture.plan_columns,
                limits=GenerationLimits(max_rows=100),
            )

        self.assertIn("rows", str(caught.exception))

    def test_too_many_categories_fails_before_generating(self) -> None:
        spec = SalesFixture.spec(
            columns=(
                GenerationColumn(
                    column_key="region",
                    rule=CategoricalRule(
                        values=tuple(f"region-{index}" for index in range(50))
                    ),
                ),
            ),
            constraints=(),
        )

        with self.assertRaises(GenerationError):
            generate_dataset(
                spec,
                columns=(SalesFixture.plan_columns[1],),
                limits=GenerationLimits(max_categories=10),
            )


class EngineIntegrationTests(unittest.TestCase):
    """Generation is a source step: no inputs, and it feeds the rest of a plan."""

    def _recipe(self):
        from scripts.data_analysis_agent.runtime.execution.contracts import (
            NativeRecipe,
        )
        from scripts.data_analysis_agent.runtime.execution.native.engine import (
            engine_version,
        )
        from scripts.data_analysis_agent.runtime.models.expressions import (
            ColumnExpression,
            CompareExpression,
            LiteralExpression,
        )
        from scripts.data_analysis_agent.runtime.models.plans import (
            FilterRowsStep,
            GenerateDatasetStep,
            PlanStepEstimate,
            StepProvenance,
        )

        columns = (
            column("identifier", PlanDataType.STRING),
            column("amount", PlanDataType.CURRENCY, unit="USD"),
        )
        generate = GenerateDatasetStep(
            step_id="generate_rows",
            executor="native",
            output_alias="generated",
            generation=SyntheticDatasetSpec(
                dataset_name="demo",
                row_count=50,
                seed=42,
                columns=(
                    GenerationColumn(
                        column_key="identifier",
                        rule=UniqueIdRule(prefix="TX", width=4),
                    ),
                    GenerationColumn(
                        column_key="amount",
                        rule=DecimalRangeRule(
                            minimum_minor_units=100,
                            maximum_minor_units=100_000,
                            scale=2,
                        ),
                    ),
                ),
            ),
            expected_schema=columns,
            estimate=PlanStepEstimate(),
            provenance=StepProvenance(
                generated=True,
                description="Seeded synthetic rows.",
            ),
        )
        keep_large = FilterRowsStep(
            step_id="keep_large",
            executor="native",
            input_alias="generated",
            output_alias="large",
            predicate=CompareExpression(
                operator="greater_than",
                left=ColumnExpression(column_key="amount"),
                right=LiteralExpression(
                    value=500,
                    data_type="currency",
                    unit="USD",
                ),
            ),
            expected_schema=columns,
            estimate=PlanStepEstimate(rows_scanned=50),
            provenance=StepProvenance(description="Filtered generated rows."),
        )
        return NativeRecipe(
            engine_version=engine_version(),
            semantics_version="2.0",
            steps=(generate, keep_large),
            result_alias="large",
        )

    def _run(self):
        import tempfile
        from pathlib import Path

        from scripts.data_analysis_agent.runtime.execution.native.engine import (
            execute_recipe,
        )

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return execute_recipe(
            self._recipe(),
            output_path=Path(directory.name) / "out.arrow",
        )

    def test_a_generation_only_recipe_needs_no_input_tables(self) -> None:
        result = self._run()

        self.assertTrue(result.succeeded, result.failure_message)
        self.assertGreater(result.row_count, 0)

    def test_generation_feeds_downstream_steps_with_correct_metrics(self) -> None:
        result = self._run()

        self.assertEqual(
            [
                (metric.step_id, metric.input_rows, metric.output_rows)
                for metric in result.step_metrics
            ][0],
            ("generate_rows", 0, 50),
        )
        self.assertEqual(result.step_metrics[1].input_rows, 50)

    def test_a_generated_result_replays_to_the_same_hash(self) -> None:
        self.assertEqual(self._run().content_hash, self._run().content_hash)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
