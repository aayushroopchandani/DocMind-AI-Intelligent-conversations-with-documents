from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..models.expressions import (
    BetweenExpression,
    BinaryExpression,
    BooleanExpression,
    CaseWhenExpression,
    CastExpression,
    CoalesceExpression,
    ColumnExpression,
    CompareExpression,
    DatePartExpression,
    DateTruncExpression,
    Expression,
    ExpressionDataType,
    LiteralExpression,
    NullCheckExpression,
    SetExpression,
    StringTransformExpression,
    UnaryExpression,
)
from ..models.plans import PlanColumn, PlanDataType
from .type_system import (
    NUMERIC_TYPES as _NUMERIC,
    ORDERABLE_TYPES as _ORDERABLE,
    literal_matches as _literal_matches,
    types_compatible,
    wider_numeric_type as _wider_numeric_type,
)


_EXPRESSION_TO_PLAN_TYPE = {
    ExpressionDataType.STRING: PlanDataType.STRING,
    ExpressionDataType.INTEGER: PlanDataType.INTEGER,
    ExpressionDataType.NUMBER: PlanDataType.NUMBER,
    ExpressionDataType.DECIMAL: PlanDataType.DECIMAL,
    ExpressionDataType.CURRENCY: PlanDataType.CURRENCY,
    ExpressionDataType.PERCENTAGE: PlanDataType.PERCENTAGE,
    ExpressionDataType.BOOLEAN: PlanDataType.BOOLEAN,
    ExpressionDataType.DATE: PlanDataType.DATE,
    ExpressionDataType.PERIOD: PlanDataType.PERIOD,
}


@dataclass(frozen=True, slots=True)
class ExpressionType:
    data_type: PlanDataType
    unit: str | None = None
    nullable: bool = True


@dataclass(frozen=True, slots=True)
class ExpressionProblem:
    code: str
    message: str
    path: str


def validate_expression(
    expression: Expression,
    *,
    schema: dict[str, PlanColumn],
    path: str,
) -> tuple[ExpressionType, tuple[ExpressionProblem, ...]]:
    problems: list[ExpressionProblem] = []

    def problem(code: str, message: str, node_path: str) -> None:
        problems.append(ExpressionProblem(code, message, node_path))

    def infer(node: Expression, node_path: str) -> ExpressionType:
        if isinstance(node, ColumnExpression):
            column = schema.get(node.column_key)
            if column is None:
                return ExpressionType(PlanDataType.UNKNOWN)
            return ExpressionType(column.data_type, column.unit, column.nullable)
        if isinstance(node, LiteralExpression):
            data_type = _EXPRESSION_TO_PLAN_TYPE[node.data_type]
            if not _literal_matches(node.value, data_type):
                problem(
                    "expression_literal_type_mismatch",
                    "Literal value does not match its declared data type.",
                    node_path,
                )
            return ExpressionType(data_type, node.unit, node.value is None)
        if isinstance(node, UnaryExpression):
            operand = infer(node.operand, f"{node_path}.operand")
            if node.operator == "not":
                if operand.data_type != PlanDataType.BOOLEAN:
                    problem(
                        "expression_boolean_type_mismatch",
                        "Boolean not requires a boolean operand.",
                        node_path,
                    )
                return ExpressionType(PlanDataType.BOOLEAN, nullable=operand.nullable)
            if operand.data_type not in _NUMERIC:
                problem(
                    "expression_numeric_type_mismatch",
                    "Numeric unary operators require a numeric operand.",
                    node_path,
                )
            return operand
        if isinstance(node, BinaryExpression):
            left = infer(node.left, f"{node_path}.left")
            right = infer(node.right, f"{node_path}.right")
            if left.data_type not in _NUMERIC or right.data_type not in _NUMERIC:
                problem(
                    "expression_numeric_type_mismatch",
                    "Arithmetic operators require numeric operands.",
                    node_path,
                )
            if node.operator in {"add", "subtract", "modulo"} and (
                left.unit != right.unit
            ):
                problem(
                    "expression_unit_mismatch",
                    "Add, subtract and modulo operands must use the same unit.",
                    node_path,
                )
            output_type = _wider_numeric_type(left.data_type, right.data_type)
            if node.operator == "safe_divide":
                unit = None if left.unit == right.unit else _divide_unit(left.unit, right.unit)
                output_type = PlanDataType.NUMBER
            elif node.operator == "multiply":
                unit = _multiply_unit(left.unit, right.unit)
            else:
                unit = left.unit
            return ExpressionType(
                output_type,
                unit,
                left.nullable
                or right.nullable
                or (
                    node.operator == "safe_divide"
                    and node.zero_division == "null"
                ),
            )
        if isinstance(node, CompareExpression):
            left = infer(node.left, f"{node_path}.left")
            right = infer(node.right, f"{node_path}.right")
            if node.operator in {"contains", "starts_with", "ends_with"}:
                compatible = (
                    left.data_type == PlanDataType.STRING
                    and right.data_type == PlanDataType.STRING
                )
            elif node.operator in {
                "greater_than",
                "greater_than_or_equal",
                "less_than",
                "less_than_or_equal",
            }:
                compatible = (
                    left.data_type in _ORDERABLE
                    and right.data_type in _ORDERABLE
                    and _compatible(left, right)
                )
            else:
                compatible = _compatible(left, right)
            if not compatible:
                problem(
                    "expression_comparison_type_mismatch",
                    "Comparison operands have incompatible types or units.",
                    node_path,
                )
            return ExpressionType(
                PlanDataType.BOOLEAN,
                nullable=left.nullable or right.nullable,
            )
        if isinstance(node, SetExpression):
            source = infer(node.expression, f"{node_path}.expression")
            for index, literal in enumerate(node.values):
                candidate = infer(literal, f"{node_path}.values.{index}")
                if not _compatible(source, candidate):
                    problem(
                        "expression_set_type_mismatch",
                        "Set values must match the tested expression type and unit.",
                        f"{node_path}.values.{index}",
                    )
            return ExpressionType(PlanDataType.BOOLEAN, nullable=source.nullable)
        if isinstance(node, BetweenExpression):
            source = infer(node.expression, f"{node_path}.expression")
            lower = infer(node.lower, f"{node_path}.lower")
            upper = infer(node.upper, f"{node_path}.upper")
            if source.data_type not in _ORDERABLE or not (
                _compatible(source, lower) and _compatible(source, upper)
            ):
                problem(
                    "expression_between_type_mismatch",
                    "Between bounds must match an orderable expression.",
                    node_path,
                )
            return ExpressionType(
                PlanDataType.BOOLEAN,
                nullable=source.nullable or lower.nullable or upper.nullable,
            )
        if isinstance(node, BooleanExpression):
            nullable = False
            for index, operand_node in enumerate(node.operands):
                operand = infer(operand_node, f"{node_path}.operands.{index}")
                nullable = nullable or operand.nullable
                if operand.data_type != PlanDataType.BOOLEAN:
                    problem(
                        "expression_boolean_type_mismatch",
                        "Boolean operators require boolean operands.",
                        f"{node_path}.operands.{index}",
                    )
            return ExpressionType(PlanDataType.BOOLEAN, nullable=nullable)
        if isinstance(node, CaseWhenExpression):
            result_types: list[ExpressionType] = []
            for index, branch in enumerate(node.branches):
                condition = infer(branch.condition, f"{node_path}.branches.{index}.condition")
                if condition.data_type != PlanDataType.BOOLEAN:
                    problem(
                        "expression_case_condition_not_boolean",
                        "Case conditions must be boolean.",
                        f"{node_path}.branches.{index}.condition",
                    )
                result_types.append(
                    infer(branch.result, f"{node_path}.branches.{index}.result")
                )
            result_types.append(infer(node.otherwise, f"{node_path}.otherwise"))
            return _merge_result_types(result_types, node_path, problem)
        if isinstance(node, CoalesceExpression):
            result_types = [
                infer(item, f"{node_path}.expressions.{index}")
                for index, item in enumerate(node.expressions)
            ]
            merged = _merge_result_types(result_types, node_path, problem)
            return ExpressionType(
                merged.data_type,
                merged.unit,
                all(item.nullable for item in result_types),
            )
        if isinstance(node, CastExpression):
            source = infer(node.expression, f"{node_path}.expression")
            target = _EXPRESSION_TO_PLAN_TYPE[node.target_type]
            return ExpressionType(
                target,
                (
                    source.unit
                    if source.data_type in _NUMERIC and target in _NUMERIC
                    else None
                ),
                nullable=source.nullable or node.failure_policy == "null",
            )
        if isinstance(node, DatePartExpression):
            source = infer(node.expression, f"{node_path}.expression")
            if source.data_type not in {PlanDataType.DATE, PlanDataType.PERIOD}:
                problem(
                    "expression_date_type_mismatch",
                    "Date-part extraction requires a date or period expression.",
                    node_path,
                )
            return ExpressionType(PlanDataType.INTEGER, nullable=source.nullable)
        if isinstance(node, DateTruncExpression):
            source = infer(node.expression, f"{node_path}.expression")
            if source.data_type not in {PlanDataType.DATE, PlanDataType.PERIOD}:
                problem(
                    "expression_date_type_mismatch",
                    "Date truncation requires a date or period expression.",
                    node_path,
                )
            return ExpressionType(PlanDataType.DATE, nullable=source.nullable)
        if isinstance(node, StringTransformExpression):
            source = infer(node.expression, f"{node_path}.expression")
            if source.data_type != PlanDataType.STRING:
                problem(
                    "expression_string_type_mismatch",
                    "String transformations require a string expression.",
                    node_path,
                )
            output = (
                PlanDataType.INTEGER
                if node.operation == "length"
                else PlanDataType.STRING
            )
            return ExpressionType(output, nullable=source.nullable)
        if isinstance(node, NullCheckExpression):
            infer(node.expression, f"{node_path}.expression")
            return ExpressionType(PlanDataType.BOOLEAN, nullable=False)
        raise AssertionError(f"unsupported typed expression: {type(node)!r}")

    result = infer(expression, path)
    return result, tuple(problems)


def expression_output_matches(result: ExpressionType, output: PlanColumn) -> bool:
    """Return whether an inferred expression type can fill an output column."""

    if result.nullable and not output.nullable:
        return False
    if types_compatible(
        result.data_type,
        result.unit,
        output.data_type,
        output.unit,
    ):
        return True
    # A ratio produced by safe_divide is unitless; writing it into a declared
    # percentage column is the one intentional widening the planner may use.
    return (
        output.data_type == PlanDataType.PERCENTAGE
        and result.data_type in _NUMERIC
        and result.data_type != PlanDataType.CURRENCY
        and result.unit is None
        and output.unit in {None, "%", "percent", "percentage"}
    )


def _compatible(left: ExpressionType, right: ExpressionType) -> bool:
    return types_compatible(
        left.data_type,
        left.unit,
        right.data_type,
        right.unit,
    )


def _divide_unit(left: str | None, right: str | None) -> str | None:
    if right is None:
        return left
    if left is None:
        return f"1/{right}"
    return f"{left}/{right}"


def _multiply_unit(left: str | None, right: str | None) -> str | None:
    if left is None:
        return right
    if right is None:
        return left
    return f"{left}*{right}"


def _merge_result_types(
    values: list[ExpressionType],
    path: str,
    problem: Callable[[str, str, str], None],
) -> ExpressionType:
    first = values[0]
    if any(not _compatible(first, candidate) for candidate in values[1:]):
        problem(
            "expression_branch_type_mismatch",
            "All conditional/coalesce result expressions must have compatible types.",
            path,
        )
    return ExpressionType(
        first.data_type,
        first.unit,
        any(candidate.nullable for candidate in values),
    )


__all__ = [
    "ExpressionProblem",
    "ExpressionType",
    "expression_output_matches",
    "validate_expression",
]
