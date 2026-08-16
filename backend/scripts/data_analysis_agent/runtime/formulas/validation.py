"""Validate a semantic formula against the schema it will run over.

The AST guarantees a formula is *well formed*. This checks it is *meaningful*:
that every column it names exists, that the types line up, and that the result
is something the declared output column can hold.

Structured like the plan validator — issues with codes and paths, not
exceptions — so a formula problem can be reported to the planner for its single
bounded repair in exactly the way a plan problem is.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models.plans import PlanColumn, PlanDataType
from ..planning.type_system import NUMERIC_TYPES, TEMPORAL_TYPES
from .expressions import (
    FormulaAbsolute,
    FormulaAggregate,
    FormulaArithmetic,
    FormulaBoolean,
    FormulaColumnRef,
    FormulaCompare,
    FormulaDatePart,
    FormulaExpression,
    FormulaIf,
    FormulaIfError,
    FormulaLiteral,
    FormulaNot,
    FormulaRound,
    FormulaSafeDivide,
    FormulaSpec,
    FormulaTextTransform,
    FormulaValueType,
    validate_formula_size,
)


@dataclass(frozen=True, slots=True)
class FormulaIssue:
    code: str
    message: str
    path: str


_LITERAL_TO_PLAN = {
    FormulaValueType.NUMBER: PlanDataType.NUMBER,
    FormulaValueType.TEXT: PlanDataType.STRING,
    FormulaValueType.BOOLEAN: PlanDataType.BOOLEAN,
    FormulaValueType.DATE: PlanDataType.DATE,
}


def validate_formula(
    spec: FormulaSpec,
    *,
    schema: dict[str, PlanColumn],
) -> tuple[PlanDataType, tuple[FormulaIssue, ...]]:
    """Return the formula's result type and every problem found."""

    issues: list[FormulaIssue] = []
    try:
        validate_formula_size(spec.expression)
    except ValueError as error:
        issues.append(FormulaIssue("formula_too_large", str(error), "expression"))
        return PlanDataType.UNKNOWN, tuple(issues)

    if spec.output_column_key in schema:
        issues.append(
            FormulaIssue(
                "formula_output_collides",
                f"Column '{spec.output_column_key}' already exists.",
                "output_column_key",
            )
        )

    result = _infer(spec.expression, schema, issues, "expression")
    return result, tuple(issues)


def _infer(
    expression: FormulaExpression,
    schema: dict[str, PlanColumn],
    issues: list[FormulaIssue],
    path: str,
) -> PlanDataType:
    def child(node: FormulaExpression, suffix: str) -> PlanDataType:
        return _infer(node, schema, issues, f"{path}.{suffix}")

    def report(code: str, message: str) -> None:
        issues.append(FormulaIssue(code, message, path))

    if isinstance(expression, FormulaColumnRef):
        column = schema.get(expression.column_key)
        if column is None:
            report(
                "formula_unknown_column",
                f"Formula references unknown column '{expression.column_key}'.",
            )
            return PlanDataType.UNKNOWN
        return column.data_type

    if isinstance(expression, FormulaLiteral):
        return _LITERAL_TO_PLAN[expression.value_type]

    if isinstance(expression, FormulaArithmetic):
        left = child(expression.left, "left")
        right = child(expression.right, "right")
        if not _numeric(left) or not _numeric(right):
            report(
                "formula_arithmetic_type_mismatch",
                "Arithmetic operands must be numeric.",
            )
            return PlanDataType.UNKNOWN
        return PlanDataType.NUMBER

    if isinstance(expression, FormulaSafeDivide):
        numerator = child(expression.numerator, "numerator")
        denominator = child(expression.denominator, "denominator")
        child(expression.on_zero, "on_zero")
        child(expression.on_error, "on_error")
        if not _numeric(numerator) or not _numeric(denominator):
            report(
                "formula_division_type_mismatch",
                "Division operands must be numeric.",
            )
        return PlanDataType.NUMBER

    if isinstance(expression, FormulaCompare):
        left = child(expression.left, "left")
        right = child(expression.right, "right")
        if not _comparable(left, right):
            report(
                "formula_comparison_type_mismatch",
                "Comparison operands have incompatible types.",
            )
        return PlanDataType.BOOLEAN

    if isinstance(expression, FormulaBoolean):
        for index, operand in enumerate(expression.operands):
            if child(operand, f"operands.{index}") not in {
                PlanDataType.BOOLEAN,
                PlanDataType.UNKNOWN,
            }:
                report(
                    "formula_boolean_type_mismatch",
                    "Boolean operators require boolean operands.",
                )
        return PlanDataType.BOOLEAN

    if isinstance(expression, FormulaNot):
        if child(expression.operand, "operand") not in {
            PlanDataType.BOOLEAN,
            PlanDataType.UNKNOWN,
        }:
            report(
                "formula_boolean_type_mismatch",
                "NOT requires a boolean operand.",
            )
        return PlanDataType.BOOLEAN

    if isinstance(expression, FormulaIf):
        if child(expression.condition, "condition") not in {
            PlanDataType.BOOLEAN,
            PlanDataType.UNKNOWN,
        }:
            report(
                "formula_condition_not_boolean",
                "IF requires a boolean condition.",
            )
        then_type = child(expression.then_value, "then_value")
        otherwise_type = child(expression.otherwise_value, "otherwise_value")
        if not _comparable(then_type, otherwise_type):
            report(
                "formula_branch_type_mismatch",
                "Both IF branches must produce the same kind of value.",
            )
        return then_type

    if isinstance(expression, FormulaIfError):
        value = child(expression.value, "value")
        child(expression.fallback, "fallback")
        return value

    if isinstance(expression, FormulaRound):
        if not _numeric(child(expression.value, "value")):
            report("formula_round_requires_number", "ROUND requires a number.")
        return PlanDataType.NUMBER

    if isinstance(expression, FormulaAbsolute):
        if not _numeric(child(expression.value, "value")):
            report("formula_abs_requires_number", "ABS requires a number.")
        return PlanDataType.NUMBER

    if isinstance(expression, FormulaDatePart):
        if child(expression.value, "value") not in TEMPORAL_TYPES:
            report(
                "formula_date_type_mismatch",
                "Date parts require a date value.",
            )
        return PlanDataType.INTEGER

    if isinstance(expression, FormulaTextTransform):
        if child(expression.value, "value") != PlanDataType.STRING:
            report(
                "formula_text_type_mismatch",
                "Text functions require a text value.",
            )
        return (
            PlanDataType.INTEGER
            if expression.operation == "length"
            else PlanDataType.STRING
        )

    if isinstance(expression, FormulaAggregate):
        column = schema.get(expression.column_key)
        if column is None:
            report(
                "formula_unknown_column",
                f"Formula aggregates unknown column '{expression.column_key}'.",
            )
            return PlanDataType.UNKNOWN
        if expression.function == "count":
            return PlanDataType.INTEGER
        if not _numeric(column.data_type):
            report(
                "formula_aggregate_type_mismatch",
                f"'{expression.function}' requires a numeric column.",
            )
        return PlanDataType.NUMBER

    report(
        "formula_unsupported_node",
        f"Unsupported formula node '{type(expression).__name__}'.",
    )
    return PlanDataType.UNKNOWN


def _numeric(data_type: PlanDataType) -> bool:
    return data_type in NUMERIC_TYPES or data_type is PlanDataType.UNKNOWN


def _comparable(left: PlanDataType, right: PlanDataType) -> bool:
    if PlanDataType.UNKNOWN in {left, right}:
        return True
    if _numeric(left) and _numeric(right):
        return True
    return left == right


__all__ = ["FormulaIssue", "validate_formula"]
