"""Phase 9.7 semantic spreadsheet formulas and the formula compiler.

The acceptance criteria these cover:

* column renames and movement resolve through keys, not guessed names;
* relative references fill correctly from the first to the last row;
* division-by-zero and null behaviour match the specification;
* unsafe and unknown functions fail before a patch could be created;
* the server-side preview agrees with what the compiled formula would show.
"""

from __future__ import annotations

import unittest

import polars as pl

from scripts.data_analysis_agent.runtime.execution.native.expression_compiler import (
    compile_expression,
)
from scripts.data_analysis_agent.runtime.formulas import (
    FormulaCompilationError,
    FormulaPlacement,
    FormulaSafetyError,
    FormulaSpec,
    audit_compiled_formula,
    compile_formula,
    formula_column_keys,
    is_injection_risk,
    is_previewable,
    neutralize_text,
    to_native_expression,
    validate_formula,
)
from scripts.data_analysis_agent.runtime.formulas.expressions import (
    FormulaAggregate,
    FormulaArithmetic,
    FormulaColumnRef,
    FormulaCompare,
    FormulaIf,
    FormulaIfError,
    FormulaLiteral,
    FormulaRound,
    FormulaSafeDivide,
    FormulaTextTransform,
)
from scripts.data_analysis_agent.runtime.formulas.native import (
    FormulaNotPreviewableError,
)
from scripts.data_analysis_agent.runtime.models.plans import (
    PlanColumn,
    PlanDataType,
)


def column(key: str, data_type: PlanDataType):
    return PlanColumn(key=key, label=key.title(), data_type=data_type)


SCHEMA = {
    "revenue": column("revenue", PlanDataType.NUMBER),
    "cost": column("cost", PlanDataType.NUMBER),
    "name": column("name", PlanDataType.STRING),
}


def placement(**overrides) -> FormulaPlacement:
    values = {
        "columns": {"revenue": "C", "cost": "D", "name": "B"},
        "first_data_row": 2,
        "last_data_row": 101,
    }
    values.update(overrides)
    return FormulaPlacement(**values)


def profit_margin() -> FormulaSpec:
    """The worked example from the Phase 9.7 plan document."""

    return FormulaSpec(
        output_column_key="profit_margin",
        expression=FormulaSafeDivide(
            numerator=FormulaArithmetic(
                operator="subtract",
                left=FormulaColumnRef(column_key="revenue"),
                right=FormulaColumnRef(column_key="cost"),
            ),
            denominator=FormulaColumnRef(column_key="revenue"),
            on_zero=FormulaLiteral(value=0, value_type="number"),
            on_error=FormulaLiteral(value=0, value_type="number"),
        ),
        number_format="0.00%",
    )


class CompilationTests(unittest.TestCase):
    def test_the_plan_example_compiles_to_a_guarded_formula(self) -> None:
        compiled = compile_formula(profit_margin(), placement())

        self.assertEqual(
            compiled.formula,
            "=IFERROR(IF(C2=0,0,(C2-D2)/C2),0)",
        )

    def test_references_come_from_placement_not_from_names(self) -> None:
        moved = compile_formula(
            profit_margin(),
            placement(columns={"revenue": "Q", "cost": "R"}),
        )

        # The same formula, pointed at wherever the columns actually landed.
        self.assertEqual(moved.formula, "=IFERROR(IF(Q2=0,0,(Q2-R2)/Q2),0)")

    def test_an_unplaced_column_fails_instead_of_guessing(self) -> None:
        with self.assertRaises(FormulaCompilationError):
            compile_formula(profit_margin(), placement(columns={"revenue": "C"}))

    def test_the_seed_fills_from_the_first_to_the_last_data_row(self) -> None:
        compiled = compile_formula(
            profit_margin(),
            placement(first_data_row=5, last_data_row=204),
        )

        self.assertEqual(compiled.seed_row, 5)
        self.assertEqual(compiled.fill_through_row, 204)
        self.assertEqual(compiled.fill_row_count, 200)
        self.assertIn("C5", compiled.formula)

    def test_fill_none_writes_only_the_seed_row(self) -> None:
        spec = profit_margin().model_copy(update={"fill": "none"})

        compiled = compile_formula(spec, placement())

        self.assertEqual(compiled.fill_row_count, 1)

    def test_an_aggregate_anchors_its_range_so_filling_cannot_slide_it(self) -> None:
        spec = FormulaSpec(
            output_column_key="share",
            expression=FormulaSafeDivide(
                numerator=FormulaColumnRef(column_key="revenue"),
                denominator=FormulaAggregate(
                    function="sum",
                    column_key="revenue",
                ),
                on_zero=FormulaLiteral(value=0, value_type="number"),
                on_error=FormulaLiteral(value=0, value_type="number"),
            ),
        )

        compiled = compile_formula(spec, placement())

        self.assertIn("SUM(C$2:C$101)", compiled.formula)

    def test_lineage_records_the_coordinate_each_key_resolved_to(self) -> None:
        compiled = compile_formula(profit_margin(), placement())

        self.assertEqual(compiled.coordinate_map["revenue"], "C2")
        self.assertEqual(compiled.compiler_version, "1.0")
        self.assertEqual(compiled.locale, "en-US")

    def test_an_unsupported_locale_fails_clearly(self) -> None:
        with self.assertRaises(FormulaSafetyError):
            compile_formula(profit_margin(), placement(), locale="de-DE")

    def test_literals_are_emitted_in_their_spreadsheet_form(self) -> None:
        spec = FormulaSpec(
            output_column_key="label",
            expression=FormulaIf(
                condition=FormulaCompare(
                    operator="greater_than",
                    left=FormulaColumnRef(column_key="revenue"),
                    right=FormulaLiteral(value=100, value_type="number"),
                ),
                then_value=FormulaLiteral(value="high", value_type="text"),
                otherwise_value=FormulaLiteral(value=False, value_type="boolean"),
            ),
        )

        compiled = compile_formula(spec, placement())

        self.assertEqual(compiled.formula, '=IF((C2>100),"high",FALSE)')

    def test_a_date_literal_becomes_a_date_call(self) -> None:
        spec = FormulaSpec(
            output_column_key="cutoff",
            expression=FormulaLiteral(value="2026-08-16", value_type="date"),
        )

        compiled = compile_formula(spec, placement())

        self.assertEqual(compiled.formula, "=DATE(2026,8,16)")


class SafetyTests(unittest.TestCase):
    def test_denied_functions_are_rejected(self) -> None:
        for formula in (
            '=INDIRECT("A1")',
            "=OFFSET(A1,1,1)",
            "=RAND()",
            "=TODAY()",
            '=WEBSERVICE("http://example.test")',
        ):
            with self.subTest(formula=formula):
                with self.assertRaises(FormulaSafetyError):
                    audit_compiled_formula(formula)

    def test_unknown_functions_are_rejected(self) -> None:
        with self.assertRaises(FormulaSafetyError):
            audit_compiled_formula("=TOTALLYFINE(A1)")

    def test_external_workbook_references_are_rejected(self) -> None:
        with self.assertRaises(FormulaSafetyError):
            audit_compiled_formula("=SUM([Budget.xlsx]Sheet1!A1:A5)")

    def test_a_formula_must_begin_with_an_equals_sign(self) -> None:
        with self.assertRaises(FormulaSafetyError):
            audit_compiled_formula("SUM(A1:A5)")

    def test_a_hostile_text_literal_cannot_escape_its_quotes(self) -> None:
        hostile = 'a"),INDIRECT("B1'
        spec = FormulaSpec(
            output_column_key="label",
            expression=FormulaLiteral(value=hostile, value_type="text"),
        )

        compiled = compile_formula(spec, placement())

        # The quote is doubled, so the whole thing stays one string literal
        # and the audit sees no function call at all.
        self.assertEqual(compiled.formula, '="a""),INDIRECT(""B1"')

    def test_a_function_name_inside_a_literal_is_not_a_call(self) -> None:
        # Precision matters as much as strictness: rejecting this would make
        # perfectly ordinary text unusable.
        audit_compiled_formula('=IF(A1=1,"see INDIRECT(x) in the docs","")')

    def test_an_unterminated_literal_is_rejected(self) -> None:
        with self.assertRaises(FormulaSafetyError):
            audit_compiled_formula('=CONCAT("abc)')


class InjectionGuardTests(unittest.TestCase):
    def test_dangerous_prefixes_are_recognized(self) -> None:
        for value in ("=cmd|' /c calc'!A", "+1+1", "-1+1", "@SUM(A1)"):
            with self.subTest(value=value):
                self.assertTrue(is_injection_risk(value))

    def test_ordinary_text_is_left_alone(self) -> None:
        self.assertFalse(is_injection_risk("North"))
        self.assertEqual(neutralize_text("North"), "North")

    def test_risky_text_is_stored_as_text(self) -> None:
        self.assertEqual(neutralize_text("=cmd|calc"), "'=cmd|calc")


class ValidationTests(unittest.TestCase):
    def test_a_well_typed_formula_validates_cleanly(self) -> None:
        result, issues = validate_formula(profit_margin(), schema=SCHEMA)

        self.assertEqual(issues, ())
        self.assertEqual(result, PlanDataType.NUMBER)

    def test_an_unknown_column_is_reported(self) -> None:
        spec = FormulaSpec(
            output_column_key="x",
            expression=FormulaColumnRef(column_key="not_a_column"),
        )

        _result, issues = validate_formula(spec, schema=SCHEMA)

        self.assertIn("formula_unknown_column", {issue.code for issue in issues})

    def test_arithmetic_on_text_is_reported(self) -> None:
        spec = FormulaSpec(
            output_column_key="x",
            expression=FormulaArithmetic(
                operator="add",
                left=FormulaColumnRef(column_key="name"),
                right=FormulaLiteral(value=1, value_type="number"),
            ),
        )

        _result, issues = validate_formula(spec, schema=SCHEMA)

        self.assertIn(
            "formula_arithmetic_type_mismatch",
            {issue.code for issue in issues},
        )

    def test_an_output_colliding_with_an_existing_column_is_reported(self) -> None:
        spec = profit_margin().model_copy(update={"output_column_key": "revenue"})

        _result, issues = validate_formula(spec, schema=SCHEMA)

        self.assertIn("formula_output_collides", {issue.code for issue in issues})

    def test_a_text_function_on_a_number_is_reported(self) -> None:
        spec = FormulaSpec(
            output_column_key="x",
            expression=FormulaTextTransform(
                operation="upper",
                value=FormulaColumnRef(column_key="revenue"),
            ),
        )

        _result, issues = validate_formula(spec, schema=SCHEMA)

        self.assertIn(
            "formula_text_type_mismatch",
            {issue.code for issue in issues},
        )

    def test_referenced_columns_are_reported_for_placement(self) -> None:
        self.assertEqual(
            formula_column_keys(profit_margin().expression),
            ("revenue", "cost"),
        )


class NativePreviewTests(unittest.TestCase):
    """The server-side preview must agree with what the sheet would show."""

    frame = pl.DataFrame(
        {
            "revenue": [100.0, 0.0, 250.0],
            "cost": [40.0, 10.0, 200.0],
        }
    )

    def evaluate(self, spec: FormulaSpec):
        native = to_native_expression(spec.expression)
        compiled = compile_expression(
            native,
            available_columns=frozenset(self.frame.columns),
        )
        return self.frame.select(compiled.alias("result"))["result"].to_list()

    def test_the_preview_matches_the_declared_division_behaviour(self) -> None:
        values = self.evaluate(profit_margin())

        # Row two divides by zero, so the declared `on_zero` value is used
        # rather than a spreadsheet error or a null.
        self.assertEqual(values, [0.6, 0.0, 0.2])

    def test_a_conditional_previews_the_same_branch_the_sheet_would_take(self) -> None:
        spec = FormulaSpec(
            output_column_key="tier",
            expression=FormulaIf(
                condition=FormulaCompare(
                    operator="greater_than",
                    left=FormulaColumnRef(column_key="revenue"),
                    right=FormulaLiteral(value=50, value_type="number"),
                ),
                then_value=FormulaLiteral(value="high", value_type="text"),
                otherwise_value=FormulaLiteral(value="low", value_type="text"),
            ),
        )

        self.assertEqual(self.evaluate(spec), ["high", "low", "high"])

    def test_an_aggregate_is_reported_as_not_previewable(self) -> None:
        spec = FormulaSpec(
            output_column_key="total",
            expression=FormulaAggregate(function="sum", column_key="revenue"),
        )

        self.assertFalse(is_previewable(spec.expression))
        with self.assertRaises(FormulaNotPreviewableError):
            to_native_expression(spec.expression)

    def test_iferror_is_reported_as_not_previewable(self) -> None:
        spec = FormulaSpec(
            output_column_key="safe",
            expression=FormulaIfError(
                value=FormulaColumnRef(column_key="revenue"),
                fallback=FormulaLiteral(value=0, value_type="number"),
            ),
        )

        # The native engine never produces #VALUE!, so a row-wise preview of
        # IFERROR would be misleading rather than merely incomplete.
        self.assertFalse(is_previewable(spec.expression))

    def test_rounding_previews_through_to_the_inner_value(self) -> None:
        spec = FormulaSpec(
            output_column_key="rounded",
            expression=FormulaRound(
                value=FormulaColumnRef(column_key="revenue"),
                digits=1,
            ),
        )

        self.assertTrue(is_previewable(spec.expression))
        self.assertEqual(self.evaluate(spec), [100.0, 0.0, 250.0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
