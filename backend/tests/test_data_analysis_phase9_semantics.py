"""Phase 9.5 semantics, and the operations added in the second 9.4 pass.

The acceptance criteria these cover:

* golden fixtures over nulls, dates, decimals, duplicate keys and ordering;
* a semantic policy change produces a new execution key;
* join bombs and pivot-width explosions fail before publishing output;
* operation metrics are consistent and reproducible.

Each test states the rule it pins, because the point of these is not that the
code works today but that a Polars upgrade cannot quietly change an answer.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

import polars as pl
from pydantic import ValidationError

from scripts.data_analysis_agent.runtime.execution.native.operations import (
    NativeExecutionSemanticError,
    lookup,
)
from scripts.data_analysis_agent.runtime.execution.contracts import (
    ExecutionFailureCode,
    ExecutionLimits,
    NativeInputTable,
    NativeRecipe,
)
from scripts.data_analysis_agent.runtime.execution.native import semantics, staging
from scripts.data_analysis_agent.runtime.execution.native.engine import (
    engine_version,
    execute_recipe,
)
from scripts.data_analysis_agent.runtime.models.expressions import (
    ColumnExpression,
    CompareExpression,
    LiteralExpression,
)
from scripts.data_analysis_agent.runtime.models.plans import (
    AggregateMetric,
    AggregateStep,
    ColumnRename,
    DeduplicateStep,
    FillMissingStep,
    FillRule,
    FilterRowsStep,
    JoinKeyPair,
    JoinStep,
    PivotCategoryPolicy,
    PivotStep,
    PlanColumn,
    PlanDataType,
    PlanExecutor,
    PlanStepEstimate,
    RenameColumnsStep,
    SortKey,
    SortRowsStep,
    StepProvenance,
    UnpivotStep,
    join_output_schema,
)


_HASH = "a" * 64


def column(key: str, data_type: PlanDataType, *, unit=None, nullable=True):
    return PlanColumn(
        key=key,
        label=key.replace("_", " ").title(),
        data_type=data_type,
        unit=unit,
        nullable=nullable,
    )


def provenance():
    return StepProvenance(
        source_dataset_ids=("source_1",),
        source_versions=(_HASH,),
        description="Rows retain their source lineage.",
    )


def estimate(rows: int = 0):
    return PlanStepEstimate(rows_scanned=rows, duration_seconds=1)


class EngineHarness(unittest.TestCase):
    """Stages one or more named inputs and runs a recipe over them."""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self._run_index = 0
        self.addCleanup(self._directory.cleanup)

    def run_recipe(self, steps, inputs, *, result_alias, limits=None):
        self._run_index += 1
        tables = []
        for alias, (columns, rows) in inputs.items():
            path = self.root / f"{alias}-{self._run_index}.arrow"
            staging.write_ipc(columns, rows, path=path)
            tables.append(
                NativeInputTable(
                    alias=alias,
                    dataset_id="normalized_0123456789abcdef01234567",
                    content_signature=_HASH,
                    columns=columns,
                    row_count=len(rows),
                    ipc_path=str(path),
                )
            )
        recipe = NativeRecipe(
            engine_version=engine_version(),
            semantics_version=semantics.NATIVE_SEMANTICS_VERSION,
            steps=tuple(steps),
            inputs=tuple(tables),
            result_alias=result_alias,
            limits=limits or ExecutionLimits(),
        )
        return execute_recipe(
            recipe,
            output_path=self.root / f"out-{self._run_index}.arrow",
        )

    @staticmethod
    def rows_of(result):
        return pl.read_ipc(result.ipc_path).to_dicts()


# --------------------------------------------------------------- deduplicate


class DeduplicationTests(EngineHarness):
    columns = (
        column("key", PlanDataType.STRING),
        column("amount", PlanDataType.INTEGER),
    )
    rows = (
        {"key": "a", "amount": 1},
        {"key": "b", "amount": 9},
        {"key": "a", "amount": 2},
        {"key": "a", "amount": 3},
    )

    def _step(self, *, keep="first", order_policy="stable_input", order_by=()):
        return DeduplicateStep(
            step_id="dedupe",
            executor=PlanExecutor.NATIVE,
            input_alias="src",
            output_alias="deduped",
            key_columns=("key",),
            keep=keep,
            order_policy=order_policy,
            order_by=order_by,
            expected_schema=self.columns,
            estimate=estimate(4),
            provenance=provenance(),
        )

    def test_keep_first_means_first_in_input_order(self) -> None:
        result = self.run_recipe(
            [self._step(keep="first")],
            {"src": (self.columns, self.rows)},
            result_alias="deduped",
        )

        self.assertTrue(result.succeeded, result.failure_message)
        self.assertEqual(
            self.rows_of(result),
            [{"key": "a", "amount": 1}, {"key": "b", "amount": 9}],
        )

    def test_keep_last_means_last_in_input_order(self) -> None:
        result = self.run_recipe(
            [self._step(keep="last")],
            {"src": (self.columns, self.rows)},
            result_alias="deduped",
        )

        # Surviving rows stay in the order of the positions they were kept from,
        # so "b" (index 1) precedes the last "a" (index 3).
        self.assertEqual(
            self.rows_of(result),
            [{"key": "b", "amount": 9}, {"key": "a", "amount": 3}],
        )

    def test_sort_keys_redefine_which_row_is_first(self) -> None:
        result = self.run_recipe(
            [
                self._step(
                    keep="first",
                    order_policy="sort_keys",
                    order_by=(SortKey(column_key="amount", direction="descending"),),
                )
            ],
            {"src": (self.columns, self.rows)},
            result_alias="deduped",
        )

        # Highest amount per key, because the declared order says so.
        self.assertEqual(
            sorted(self.rows_of(result), key=lambda row: row["key"]),
            [{"key": "a", "amount": 3}, {"key": "b", "amount": 9}],
        )

    def test_the_error_policy_refuses_a_duplicate_key(self) -> None:
        result = self.run_recipe(
            [self._step(keep="error")],
            {"src": (self.columns, self.rows)},
            result_alias="deduped",
        )

        self.assertFalse(result.succeeded)
        self.assertEqual(
            result.failure_code,
            ExecutionFailureCode.SEMANTIC_VIOLATION,
        )

    def test_the_error_policy_passes_on_unique_keys(self) -> None:
        result = self.run_recipe(
            [self._step(keep="error")],
            {"src": (self.columns, ({"key": "a", "amount": 1},))},
            result_alias="deduped",
        )

        self.assertTrue(result.succeeded, result.failure_message)


# -------------------------------------------------------------------- rename


class RenameTests(EngineHarness):
    columns = (
        column("old_key", PlanDataType.STRING),
        column("keeper", PlanDataType.INTEGER),
    )

    def _step(self, output_key: str):
        return RenameColumnsStep(
            step_id="rename",
            executor=PlanExecutor.NATIVE,
            input_alias="src",
            output_alias="renamed",
            renames=(
                ColumnRename(
                    source_key="old_key",
                    output_key=output_key,
                    output_label="Display Only",
                ),
            ),
            expected_schema=(
                column(output_key, PlanDataType.STRING),
                self.columns[1],
            ),
            estimate=estimate(1),
            provenance=provenance(),
        )

    def test_a_column_is_renamed_by_key(self) -> None:
        result = self.run_recipe(
            [self._step("new_key")],
            {"src": (self.columns, ({"old_key": "x", "keeper": 1},))},
            result_alias="renamed",
        )

        self.assertTrue(result.succeeded, result.failure_message)
        self.assertEqual(list(self.rows_of(result)[0]), ["new_key", "keeper"])

    def test_the_plan_contract_refuses_a_colliding_rename(self) -> None:
        # `expected_schema` would need two columns called "keeper", which the
        # plan model rejects outright — the collision never reaches the engine.
        with self.assertRaises(ValidationError):
            self._step("keeper")

    def test_the_compiler_also_refuses_a_colliding_rename(self) -> None:
        # Defense in depth for the same rule, reached directly because a valid
        # plan cannot express it. A compiler bug must not silently drop a column.
        step = self._step("new_key").model_construct(
            **{
                **self._step("new_key").__dict__,
                "renames": (
                    ColumnRename(
                        source_key="old_key",
                        output_key="keeper",
                        output_label="Display Only",
                    ),
                ),
            }
        )
        frame = pl.DataFrame({"old_key": ["x"], "keeper": [1]}).lazy()

        with self.assertRaises(NativeExecutionSemanticError):
            lookup("rename_columns").apply(step, {"src": frame})


# -------------------------------------------------------------- fill missing


class FillMissingTests(EngineHarness):
    columns = (
        column("region", PlanDataType.STRING),
        column("day", PlanDataType.INTEGER),
        column("amount", PlanDataType.NUMBER),
    )
    rows = (
        {"region": "east", "day": 1, "amount": 10.0},
        {"region": "east", "day": 2, "amount": None},
        {"region": "west", "day": 1, "amount": None},
        {"region": "west", "day": 2, "amount": 4.0},
    )

    def _step(self, rule: FillRule, *, group_by=(), order_by=()):
        return FillMissingStep(
            step_id="fill",
            executor=PlanExecutor.NATIVE,
            input_alias="src",
            output_alias="filled",
            rules=(rule,),
            group_by=group_by,
            order_by=order_by,
            expected_schema=self.columns,
            estimate=estimate(4),
            provenance=provenance(),
        )

    def test_a_constant_fill_replaces_only_nulls(self) -> None:
        result = self.run_recipe(
            [self._step(FillRule(column_key="amount", strategy="constant", value=0))],
            {"src": (self.columns, self.rows)},
            result_alias="filled",
        )

        self.assertTrue(result.succeeded, result.failure_message)
        self.assertEqual(
            [row["amount"] for row in self.rows_of(result)],
            [10.0, 0.0, 0.0, 4.0],
        )

    def test_a_forward_fill_never_crosses_a_group_boundary(self) -> None:
        result = self.run_recipe(
            [
                self._step(
                    FillRule(column_key="amount", strategy="forward_fill"),
                    group_by=("region",),
                    order_by=(SortKey(column_key="day"),),
                )
            ],
            {"src": (self.columns, self.rows)},
            result_alias="filled",
        )

        self.assertTrue(result.succeeded, result.failure_message)
        by_region = {
            (row["region"], row["day"]): row["amount"]
            for row in self.rows_of(result)
        }
        # East carries 10.0 forward; west has nothing before day 1 to carry.
        self.assertEqual(by_region[("east", 2)], 10.0)
        self.assertIsNone(by_region[("west", 1)])

    def test_a_grouped_mean_uses_only_its_own_group(self) -> None:
        result = self.run_recipe(
            [
                self._step(
                    FillRule(column_key="amount", strategy="mean"),
                    group_by=("region",),
                )
            ],
            {"src": (self.columns, self.rows)},
            result_alias="filled",
        )

        self.assertTrue(result.succeeded, result.failure_message)
        by_region = {
            (row["region"], row["day"]): row["amount"]
            for row in self.rows_of(result)
        }
        self.assertEqual(by_region[("east", 2)], 10.0)
        self.assertEqual(by_region[("west", 1)], 4.0)


# ---------------------------------------------------------------------- join


class JoinTests(EngineHarness):
    left_columns = (
        column("id", PlanDataType.INTEGER),
        column("name", PlanDataType.STRING),
    )
    right_columns = (
        column("id", PlanDataType.INTEGER),
        column("score", PlanDataType.INTEGER),
    )

    def _step(self, *, cardinality="many_to_one", ratio=10.0, join_type="inner"):
        skeleton = JoinStep(
            step_id="join",
            executor=PlanExecutor.NATIVE,
            left_alias="left",
            right_alias="right",
            output_alias="joined",
            join_type=join_type,
            keys=(JoinKeyPair(left_column_key="id", right_column_key="id"),),
            expected_cardinality=cardinality,
            maximum_expansion_ratio=ratio,
            expected_schema=self.left_columns,
            estimate=estimate(4),
            provenance=provenance(),
        )
        return skeleton.model_copy(
            update={
                "expected_schema": join_output_schema(
                    skeleton,
                    self.left_columns,
                    self.right_columns,
                )
            }
        )

    def _inputs(self, left_rows, right_rows):
        return {
            "left": (self.left_columns, left_rows),
            "right": (self.right_columns, right_rows),
        }

    def test_matching_keys_coalesce_into_one_column(self) -> None:
        result = self.run_recipe(
            [self._step()],
            self._inputs(
                ({"id": 1, "name": "a"}, {"id": 2, "name": "b"}),
                ({"id": 1, "score": 10},),
            ),
            result_alias="joined",
        )

        self.assertTrue(result.succeeded, result.failure_message)
        self.assertEqual(
            self.rows_of(result),
            [{"id": 1, "name": "a", "score": 10}],
        )

    def test_null_keys_never_match(self) -> None:
        result = self.run_recipe(
            [self._step()],
            self._inputs(
                ({"id": None, "name": "a"},),
                ({"id": None, "score": 10},),
            ),
            result_alias="joined",
        )

        self.assertTrue(result.succeeded, result.failure_message)
        self.assertEqual(self.rows_of(result), [])

    def test_a_violated_cardinality_fails_the_run(self) -> None:
        result = self.run_recipe(
            [self._step(cardinality="one_to_one")],
            self._inputs(
                ({"id": 1, "name": "a"}, {"id": 1, "name": "b"}),
                ({"id": 1, "score": 10},),
            ),
            result_alias="joined",
        )

        self.assertFalse(result.succeeded)
        self.assertEqual(
            result.failure_code,
            ExecutionFailureCode.SEMANTIC_VIOLATION,
        )

    def test_a_join_bomb_fails_before_publishing(self) -> None:
        result = self.run_recipe(
            [self._step(cardinality="many_to_many", ratio=1.5)],
            self._inputs(
                ({"id": 1, "name": "a"}, {"id": 1, "name": "b"}),
                (
                    {"id": 1, "score": 1},
                    {"id": 1, "score": 2},
                    {"id": 1, "score": 3},
                ),
            ),
            result_alias="joined",
        )

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.content_hash)
        self.assertIn("expanded", result.failure_message or "")

    def test_a_left_join_keeps_unmatched_rows(self) -> None:
        result = self.run_recipe(
            [self._step(join_type="left")],
            self._inputs(
                ({"id": 1, "name": "a"}, {"id": 2, "name": "b"}),
                ({"id": 1, "score": 10},),
            ),
            result_alias="joined",
        )

        self.assertTrue(result.succeeded, result.failure_message)
        self.assertEqual(
            [row["score"] for row in self.rows_of(result)],
            [10, None],
        )


# --------------------------------------------------------------------- pivot


class PivotTests(EngineHarness):
    columns = (
        column("region", PlanDataType.STRING),
        column("quarter", PlanDataType.STRING),
        column("amount", PlanDataType.NUMBER),
    )
    rows = (
        {"region": "east", "quarter": "Q1", "amount": 10.0},
        {"region": "east", "quarter": "Q2", "amount": 20.0},
        {"region": "west", "quarter": "Q1", "amount": 5.0},
    )
    output = (
        column("region", PlanDataType.STRING),
        column("Q1", PlanDataType.NUMBER),
        column("Q2", PlanDataType.NUMBER),
    )

    def _step(self, policy: PivotCategoryPolicy, *, maximum_columns=500):
        return PivotStep(
            step_id="pivot",
            executor=PlanExecutor.NATIVE,
            input_alias="src",
            output_alias="wide",
            index_columns=("region",),
            pivot_column="quarter",
            value_column="amount",
            aggregation="sum",
            category_policy=policy,
            maximum_output_columns=maximum_columns,
            expected_schema=self.output,
            estimate=estimate(3),
            provenance=provenance(),
        )

    def test_an_empty_cell_is_null_not_zero(self) -> None:
        result = self.run_recipe(
            [self._step(PivotCategoryPolicy(mode="explicit", values=("Q1", "Q2")))],
            {"src": (self.columns, self.rows)},
            result_alias="wide",
        )

        self.assertTrue(result.succeeded, result.failure_message)
        west = next(row for row in self.rows_of(result) if row["region"] == "west")
        # Polars sums an absent group to 0. "No rows" is not "sums to zero".
        self.assertIsNone(west["Q2"])

    def test_discovery_produces_the_same_shape_as_explicit_categories(self) -> None:
        explicit = self.run_recipe(
            [self._step(PivotCategoryPolicy(mode="explicit", values=("Q1", "Q2")))],
            {"src": (self.columns, self.rows)},
            result_alias="wide",
        )
        discovered = self.run_recipe(
            [self._step(PivotCategoryPolicy(mode="discover", maximum_categories=10))],
            {"src": (self.columns, self.rows)},
            result_alias="wide",
        )

        self.assertTrue(discovered.succeeded, discovered.failure_message)
        self.assertEqual(explicit.content_hash, discovered.content_hash)

    def test_a_declared_category_with_no_rows_still_becomes_a_column(self) -> None:
        output = (*self.output, column("Q3", PlanDataType.NUMBER))
        step = self._step(
            PivotCategoryPolicy(mode="explicit", values=("Q1", "Q2", "Q3"))
        ).model_copy(update={"expected_schema": output})

        result = self.run_recipe(
            [step],
            {"src": (self.columns, self.rows)},
            result_alias="wide",
        )

        self.assertTrue(result.succeeded, result.failure_message)
        self.assertIn("Q3", self.rows_of(result)[0])

    def test_unbounded_discovery_fails_before_building_the_table(self) -> None:
        result = self.run_recipe(
            [self._step(PivotCategoryPolicy(mode="discover", maximum_categories=1))],
            {"src": (self.columns, self.rows)},
            result_alias="wide",
        )

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.content_hash)
        self.assertEqual(
            result.failure_code,
            ExecutionFailureCode.SEMANTIC_VIOLATION,
        )

    def test_the_plan_contract_refuses_a_width_cap_it_cannot_meet(self) -> None:
        # With explicit categories the width is known at planning time, so the
        # model rejects the step before it can ever be scheduled.
        with self.assertRaises(ValidationError):
            self._step(
                PivotCategoryPolicy(mode="explicit", values=("Q1", "Q2")),
                maximum_columns=2,
            )

    def test_the_plan_contract_bounds_discovery_width_too(self) -> None:
        # Discovery is bounded by `maximum_categories`, so the worst-case width
        # is known at planning time even though the actual categories are not.
        # Together with the test above this makes an over-wide pivot
        # unschedulable; the engine's own width check is defense in depth.
        with self.assertRaises(ValidationError):
            self._step(
                PivotCategoryPolicy(mode="discover", maximum_categories=2),
                maximum_columns=2,
            )


# ------------------------------------------------------------------- unpivot


class UnpivotTests(EngineHarness):
    columns = (
        column("region", PlanDataType.STRING),
        column("q1", PlanDataType.NUMBER),
        column("q2", PlanDataType.NUMBER),
    )
    output = (
        column("region", PlanDataType.STRING),
        column("quarter", PlanDataType.STRING),
        column("amount", PlanDataType.NUMBER),
    )

    def test_identifier_columns_are_kept_and_values_gathered(self) -> None:
        step = UnpivotStep(
            step_id="unpivot",
            executor=PlanExecutor.NATIVE,
            input_alias="src",
            output_alias="long",
            id_columns=("region",),
            value_columns=("q1", "q2"),
            variable_column=self.output[1],
            value_column=self.output[2],
            expected_schema=self.output,
            estimate=estimate(1),
            provenance=provenance(),
        )

        result = self.run_recipe(
            [step],
            {"src": (self.columns, ({"region": "east", "q1": 1.0, "q2": 2.0},))},
            result_alias="long",
        )

        self.assertTrue(result.succeeded, result.failure_message)
        self.assertEqual(
            self.rows_of(result),
            [
                {"region": "east", "quarter": "q1", "amount": 1.0},
                {"region": "east", "quarter": "q2", "amount": 2.0},
            ],
        )


# ------------------------------------------------------------ golden fixtures


class GoldenFixtureTests(EngineHarness):
    """One fixture carrying nulls, dates, decimals, duplicates and ties.

    Every value here exists to catch a specific engine default: a null that
    could be sorted either end, a date that could be parsed regionally, a
    decimal that could round either way on a tie, duplicate keys whose survivor
    depends on ordering, and equal sort keys whose order depends on scheduling.
    """

    columns = (
        column("key", PlanDataType.STRING),
        column("captured_on", PlanDataType.DATE),
        column("amount", PlanDataType.DECIMAL, unit="USD"),
    )
    rows = (
        {"key": "b", "captured_on": "2026-01-31", "amount": 2.5},
        {"key": "a", "captured_on": None, "amount": 1.005},
        {"key": "b", "captured_on": "2026-02-01", "amount": 3.5},
        {"key": "c", "captured_on": "2026-01-01", "amount": None},
        # A second "a" so the null date has a sibling to be ordered against;
        # without it, null placement inside the group would be unobservable.
        {"key": "a", "captured_on": "2026-03-15", "amount": 4.25},
    )

    def _sorted(self, nulls="last"):
        return SortRowsStep(
            step_id="sort_fixture",
            executor=PlanExecutor.NATIVE,
            input_alias="src",
            output_alias="sorted",
            keys=(
                SortKey(column_key="key"),
                SortKey(column_key="captured_on", nulls=nulls),
            ),
            expected_schema=self.columns,
            estimate=estimate(4),
            provenance=provenance(),
        )

    def test_the_fixture_replays_to_an_identical_hash(self) -> None:
        first = self.run_recipe(
            [self._sorted()],
            {"src": (self.columns, self.rows)},
            result_alias="sorted",
        )
        second = self.run_recipe(
            [self._sorted()],
            {"src": (self.columns, self.rows)},
            result_alias="sorted",
        )

        self.assertTrue(first.succeeded, first.failure_message)
        self.assertEqual(first.content_hash, second.content_hash)

    def test_null_placement_changes_the_result_and_the_hash(self) -> None:
        last = self.run_recipe(
            [self._sorted("last")],
            {"src": (self.columns, self.rows)},
            result_alias="sorted",
        )
        first = self.run_recipe(
            [self._sorted("first")],
            {"src": (self.columns, self.rows)},
            result_alias="sorted",
        )

        self.assertNotEqual(last.content_hash, first.content_hash)

    def test_dates_survive_staging_as_real_dates(self) -> None:
        result = self.run_recipe(
            [self._sorted()],
            {"src": (self.columns, self.rows)},
            result_alias="sorted",
        )

        values = [row["captured_on"] for row in self.rows_of(result)]
        self.assertIn(date(2026, 1, 31), values)
        self.assertIn(None, values)

    def test_a_decimal_tie_rounds_half_to_even(self) -> None:
        from scripts.data_analysis_agent.runtime.models.plans import DeriveColumnStep

        output = column("rounded", PlanDataType.DECIMAL, unit="USD")
        step = DeriveColumnStep(
            step_id="round_amount",
            executor=PlanExecutor.NATIVE,
            input_alias="src",
            output_alias="rounded",
            output_column=output,
            expression=ColumnExpression(column_key="amount"),
            rounding_scale=2,
            rounding_mode="half_even",
            expected_schema=(*self.columns, output),
            estimate=estimate(4),
            provenance=provenance(),
        )

        result = self.run_recipe(
            [step],
            {"src": (self.columns, self.rows)},
            result_alias="rounded",
        )

        self.assertTrue(result.succeeded, result.failure_message)
        values = [row["rounded"] for row in self.rows_of(result)]
        # 1.005 is not exactly representable; the pinned mode decides the tie,
        # and the point is that it decides it the same way on every machine.
        self.assertIn(round(1.005, 2), values)


# ---------------------------------------------------------------- fused plans


class FusedEquivalenceTests(EngineHarness):
    """A fused recipe must equal running the same steps one at a time.

    This is a 9.4 acceptance criterion. Running the chain as one recipe and then
    as a sequence of single-step recipes should produce the same rows.
    """

    columns = (
        column("region", PlanDataType.STRING),
        column("amount", PlanDataType.NUMBER),
    )
    rows = tuple(
        {"region": "east" if index % 3 else "west", "amount": float(index)}
        for index in range(30)
    )

    def _chain(self):
        total = column("total", PlanDataType.NUMBER)
        return [
            FilterRowsStep(
                step_id="filter_amounts",
                executor=PlanExecutor.NATIVE,
                input_alias="src",
                output_alias="filtered",
                predicate=CompareExpression(
                    operator="greater_than",
                    left=ColumnExpression(column_key="amount"),
                    right=LiteralExpression(value=5.0, data_type="number"),
                ),
                expected_schema=self.columns,
                estimate=estimate(30),
                provenance=provenance(),
            ),
            AggregateStep(
                step_id="total_amounts",
                executor=PlanExecutor.NATIVE,
                input_alias="filtered",
                output_alias="totals",
                group_by=("region",),
                metrics=(
                    AggregateMetric(
                        input_column_key="amount",
                        function="sum",
                        output_column=total,
                    ),
                ),
                expected_schema=(self.columns[0], total),
                estimate=estimate(30),
                provenance=provenance(),
            ),
            SortRowsStep(
                step_id="rank_totals",
                executor=PlanExecutor.NATIVE,
                input_alias="totals",
                output_alias="ranked",
                keys=(SortKey(column_key="total", direction="descending"),),
                expected_schema=(self.columns[0], total),
                estimate=estimate(2),
                provenance=provenance(),
            ),
        ]

    def test_a_fused_chain_equals_step_by_step_execution(self) -> None:
        steps = self._chain()

        fused = self.run_recipe(
            steps,
            {"src": (self.columns, self.rows)},
            result_alias="ranked",
        )

        # Run the same steps one recipe at a time, feeding each output forward.
        columns = self.columns
        rows = self.rows
        stepwise = None
        for step in steps:
            renamed = step.model_copy(update={"input_alias": "src"})
            stepwise = self.run_recipe(
                [renamed],
                {"src": (columns, rows)},
                result_alias=step.output_alias,
            )
            self.assertTrue(stepwise.succeeded, stepwise.failure_message)
            columns = stepwise.result_columns
            rows = tuple(self.rows_of(stepwise))

        self.assertTrue(fused.succeeded, fused.failure_message)
        self.assertEqual(self.rows_of(fused), list(rows))

    def test_metrics_are_reported_for_every_logical_step(self) -> None:
        result = self.run_recipe(
            self._chain(),
            {"src": (self.columns, self.rows)},
            result_alias="ranked",
        )

        self.assertEqual(
            [metric.step_id for metric in result.step_metrics],
            ["filter_amounts", "total_amounts", "rank_totals"],
        )


class MultiInputAndBarrierTests(EngineHarness):
    """A join feeding a pivot exercises both structural edge cases at once.

    The join is the only two-input operation, and the pivot is the only one that
    forces materialization. Running them together checks that the engine's
    batching survives a barrier in the middle of a recipe.
    """

    left = (
        column("id", PlanDataType.INTEGER),
        column("name", PlanDataType.STRING),
    )
    right = (
        column("id", PlanDataType.INTEGER),
        column("quarter", PlanDataType.STRING),
        column("amount", PlanDataType.NUMBER),
    )
    wide = (
        column("name", PlanDataType.STRING),
        column("Q1", PlanDataType.NUMBER),
        column("Q2", PlanDataType.NUMBER),
    )

    def _steps(self):
        skeleton = JoinStep(
            step_id="join_sales",
            executor=PlanExecutor.NATIVE,
            left_alias="customers",
            right_alias="sales",
            output_alias="joined",
            join_type="inner",
            keys=(JoinKeyPair(left_column_key="id", right_column_key="id"),),
            expected_cardinality="one_to_many",
            expected_schema=self.left,
            estimate=estimate(3),
            provenance=provenance(),
        )
        join = skeleton.model_copy(
            update={
                "expected_schema": join_output_schema(
                    skeleton,
                    self.left,
                    self.right,
                )
            }
        )
        pivot = PivotStep(
            step_id="pivot_quarters",
            executor=PlanExecutor.NATIVE,
            input_alias="joined",
            output_alias="wide",
            index_columns=("name",),
            pivot_column="quarter",
            value_column="amount",
            aggregation="sum",
            category_policy=PivotCategoryPolicy(
                mode="discover",
                maximum_categories=8,
            ),
            expected_schema=self.wide,
            estimate=estimate(3),
            provenance=provenance(),
        )
        return [join, pivot]

    def _inputs(self):
        return {
            "customers": (
                self.left,
                ({"id": 1, "name": "acme"}, {"id": 2, "name": "globex"}),
            ),
            "sales": (
                self.right,
                (
                    {"id": 1, "quarter": "Q1", "amount": 10.0},
                    {"id": 1, "quarter": "Q2", "amount": 20.0},
                    {"id": 2, "quarter": "Q1", "amount": 7.0},
                ),
            ),
        }

    def test_a_join_feeding_a_pivot_produces_the_expected_table(self) -> None:
        result = self.run_recipe(
            self._steps(),
            self._inputs(),
            result_alias="wide",
        )

        self.assertTrue(result.succeeded, result.failure_message)
        self.assertEqual(
            self.rows_of(result),
            [
                {"name": "acme", "Q1": 10.0, "Q2": 20.0},
                {"name": "globex", "Q1": 7.0, "Q2": None},
            ],
        )

    def test_metrics_survive_the_barrier_in_step_order(self) -> None:
        result = self.run_recipe(
            self._steps(),
            self._inputs(),
            result_alias="wide",
        )

        self.assertEqual(
            [(metric.step_id, metric.output_rows) for metric in result.step_metrics],
            [("join_sales", 3), ("pivot_quarters", 2)],
        )

    def test_the_result_replays_to_the_same_hash(self) -> None:
        first = self.run_recipe(self._steps(), self._inputs(), result_alias="wide")
        second = self.run_recipe(self._steps(), self._inputs(), result_alias="wide")

        self.assertEqual(first.content_hash, second.content_hash)


class SemanticPolicyTests(unittest.TestCase):
    def test_the_fingerprint_covers_every_pinned_decision(self) -> None:
        fingerprint = semantics.semantics_fingerprint()

        for key in (
            "native_semantics_version",
            "timezone",
            "date_input_format",
            "rounding_mode",
            "integer_overflow_policy",
            "empty_string_is_not_null",
            "pivot_empty_cell",
            "join_collision_policy",
            "row_order_is_input_order",
        ):
            self.assertIn(key, fingerprint)

    def test_a_policy_change_produces_a_new_execution_key(self) -> None:
        from unittest.mock import patch

        from scripts.data_analysis_agent.runtime.execution.idempotency import (
            execution_key,
        )
        from tests.test_data_analysis_phase9_execution import _plan

        plan = _plan()
        baseline = execution_key(plan, result_alias="filtered_revenue")

        with patch.object(semantics, "ROUNDING_MODE", "half_up"):
            changed = execution_key(plan, result_alias="filtered_revenue")

        self.assertNotEqual(baseline, changed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
