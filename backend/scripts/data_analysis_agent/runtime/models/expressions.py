from __future__ import annotations

import math
from collections.abc import Iterator
from enum import Enum
from typing import Annotated, Callable, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


MAX_EXPRESSION_DEPTH = 16
MAX_EXPRESSION_NODES = 256


class ExpressionDataType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    PERIOD = "period"


class LiteralExpression(BaseModel):
    kind: Literal["literal"] = "literal"
    value: JsonValue
    data_type: ExpressionDataType
    unit: str | None = Field(default=None, max_length=100)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("value")
    @classmethod
    def reject_non_finite_numbers(cls, value: JsonValue) -> JsonValue:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("expression literals must be finite")
        return value


class ColumnExpression(BaseModel):
    kind: Literal["column_ref"] = "column_ref"
    column_key: str = Field(min_length=1, max_length=120)

    model_config = ConfigDict(extra="forbid", frozen=True)


class UnaryExpression(BaseModel):
    kind: Literal["unary"] = "unary"
    operator: Literal["negate", "absolute", "not"]
    operand: Expression

    model_config = ConfigDict(extra="forbid", frozen=True)


class BinaryExpression(BaseModel):
    kind: Literal["binary"] = "binary"
    operator: Literal["add", "subtract", "multiply", "safe_divide", "modulo"]
    left: Expression
    right: Expression
    zero_division: Literal["null", "zero", "error"] | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_zero_division_policy(self) -> "BinaryExpression":
        if (self.operator == "safe_divide") != (self.zero_division is not None):
            raise ValueError(
                "zero_division is required only for safe_divide expressions"
            )
        return self


class CompareExpression(BaseModel):
    kind: Literal["compare"] = "compare"
    operator: Literal[
        "equal",
        "not_equal",
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
        "contains",
        "starts_with",
        "ends_with",
    ]
    left: Expression
    right: Expression

    model_config = ConfigDict(extra="forbid", frozen=True)


class SetExpression(BaseModel):
    kind: Literal["set_membership"] = "set_membership"
    operator: Literal["in", "not_in"]
    expression: Expression
    values: tuple[LiteralExpression, ...] = Field(min_length=1, max_length=100)

    model_config = ConfigDict(extra="forbid", frozen=True)


class BetweenExpression(BaseModel):
    kind: Literal["between"] = "between"
    expression: Expression
    lower: Expression
    upper: Expression
    inclusive: bool = True

    model_config = ConfigDict(extra="forbid", frozen=True)


class BooleanExpression(BaseModel):
    kind: Literal["boolean"] = "boolean"
    operator: Literal["and", "or"]
    operands: tuple[Expression, ...] = Field(min_length=2, max_length=24)

    model_config = ConfigDict(extra="forbid", frozen=True)


class CaseWhenBranch(BaseModel):
    condition: Expression
    result: Expression

    model_config = ConfigDict(extra="forbid", frozen=True)


class CaseWhenExpression(BaseModel):
    kind: Literal["case_when"] = "case_when"
    branches: tuple[CaseWhenBranch, ...] = Field(min_length=1, max_length=24)
    otherwise: Expression

    model_config = ConfigDict(extra="forbid", frozen=True)


class CoalesceExpression(BaseModel):
    kind: Literal["coalesce"] = "coalesce"
    expressions: tuple[Expression, ...] = Field(min_length=2, max_length=24)

    model_config = ConfigDict(extra="forbid", frozen=True)


class CastExpression(BaseModel):
    kind: Literal["cast"] = "cast"
    expression: Expression
    target_type: ExpressionDataType
    failure_policy: Literal["null", "error"] = "null"

    model_config = ConfigDict(extra="forbid", frozen=True)


class DatePartExpression(BaseModel):
    kind: Literal["date_part"] = "date_part"
    part: Literal["year", "quarter", "month", "day", "week", "weekday"]
    expression: Expression

    model_config = ConfigDict(extra="forbid", frozen=True)


class DateTruncExpression(BaseModel):
    kind: Literal["date_trunc"] = "date_trunc"
    granularity: Literal["year", "quarter", "month", "week", "day"]
    expression: Expression

    model_config = ConfigDict(extra="forbid", frozen=True)


class StringTransformExpression(BaseModel):
    kind: Literal["string_transform"] = "string_transform"
    operation: Literal["trim", "lowercase", "uppercase", "length"]
    expression: Expression

    model_config = ConfigDict(extra="forbid", frozen=True)


class NullCheckExpression(BaseModel):
    kind: Literal["null_check"] = "null_check"
    operator: Literal["is_null", "is_not_null"]
    expression: Expression

    model_config = ConfigDict(extra="forbid", frozen=True)


Expression = Annotated[
    LiteralExpression
    | ColumnExpression
    | UnaryExpression
    | BinaryExpression
    | CompareExpression
    | SetExpression
    | BetweenExpression
    | BooleanExpression
    | CaseWhenExpression
    | CoalesceExpression
    | CastExpression
    | DatePartExpression
    | DateTruncExpression
    | StringTransformExpression
    | NullCheckExpression,
    Field(discriminator="kind"),
]


for _model in (
    UnaryExpression,
    BinaryExpression,
    CompareExpression,
    SetExpression,
    BetweenExpression,
    BooleanExpression,
    CaseWhenBranch,
    CaseWhenExpression,
    CoalesceExpression,
    CastExpression,
    DatePartExpression,
    DateTruncExpression,
    StringTransformExpression,
    NullCheckExpression,
):
    _model.model_rebuild(_types_namespace={"Expression": Expression})


def expression_children(expression: Expression) -> tuple[Expression, ...]:
    if isinstance(expression, UnaryExpression):
        return (expression.operand,)
    if isinstance(expression, BinaryExpression):
        return (expression.left, expression.right)
    if isinstance(expression, CompareExpression):
        return (expression.left, expression.right)
    if isinstance(expression, SetExpression):
        return (expression.expression, *expression.values)
    if isinstance(expression, BetweenExpression):
        return (expression.expression, expression.lower, expression.upper)
    if isinstance(expression, BooleanExpression):
        return expression.operands
    if isinstance(expression, CaseWhenExpression):
        return (
            *(item for branch in expression.branches for item in (branch.condition, branch.result)),
            expression.otherwise,
        )
    if isinstance(expression, CoalesceExpression):
        return expression.expressions
    if isinstance(
        expression,
        (
            CastExpression,
            DatePartExpression,
            DateTruncExpression,
            StringTransformExpression,
            NullCheckExpression,
        ),
    ):
        return (expression.expression,)
    return ()


def walk_expression(expression: Expression) -> Iterator[tuple[Expression, int]]:
    stack: list[tuple[Expression, int]] = [(expression, 1)]
    while stack:
        current, depth = stack.pop()
        yield current, depth
        stack.extend(
            (child, depth + 1)
            for child in reversed(expression_children(current))
        )


def expression_column_keys(expression: Expression) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            node.column_key
            for node, _depth in walk_expression(expression)
            if isinstance(node, ColumnExpression)
        )
    )


def map_expression_columns(
    expression: Expression,
    resolver: Callable[[str], str],
) -> Expression:
    """Return an immutable AST with every column reference resolved once."""

    if isinstance(expression, ColumnExpression):
        return expression.model_copy(
            update={"column_key": resolver(expression.column_key)}
        )
    children = expression_children(expression)
    if not children:
        return expression
    if isinstance(expression, UnaryExpression):
        updates = {"operand": map_expression_columns(expression.operand, resolver)}
    elif isinstance(expression, BinaryExpression):
        updates = {
            "left": map_expression_columns(expression.left, resolver),
            "right": map_expression_columns(expression.right, resolver),
        }
    elif isinstance(expression, CompareExpression):
        updates = {
            "left": map_expression_columns(expression.left, resolver),
            "right": map_expression_columns(expression.right, resolver),
        }
    elif isinstance(expression, SetExpression):
        updates = {
            "expression": map_expression_columns(expression.expression, resolver)
        }
    elif isinstance(expression, BetweenExpression):
        updates = {
            "expression": map_expression_columns(expression.expression, resolver),
            "lower": map_expression_columns(expression.lower, resolver),
            "upper": map_expression_columns(expression.upper, resolver),
        }
    elif isinstance(expression, BooleanExpression):
        updates = {
            "operands": tuple(
                map_expression_columns(item, resolver) for item in expression.operands
            )
        }
    elif isinstance(expression, CaseWhenExpression):
        updates = {
            "branches": tuple(
                branch.model_copy(
                    update={
                        "condition": map_expression_columns(
                            branch.condition,
                            resolver,
                        ),
                        "result": map_expression_columns(branch.result, resolver),
                    }
                )
                for branch in expression.branches
            ),
            "otherwise": map_expression_columns(expression.otherwise, resolver),
        }
    elif isinstance(expression, CoalesceExpression):
        updates = {
            "expressions": tuple(
                map_expression_columns(item, resolver)
                for item in expression.expressions
            )
        }
    else:
        updates = {
            "expression": map_expression_columns(expression.expression, resolver)
        }
    return expression.model_copy(update=updates)


def validate_expression_size(expression: Expression) -> None:
    node_count = 0
    for _node, depth in walk_expression(expression):
        node_count += 1
        if depth > MAX_EXPRESSION_DEPTH:
            raise ValueError(
                f"expression depth cannot exceed {MAX_EXPRESSION_DEPTH}"
            )
        if node_count > MAX_EXPRESSION_NODES:
            raise ValueError(
                f"expression node count cannot exceed {MAX_EXPRESSION_NODES}"
            )


__all__ = [
    "BetweenExpression",
    "BinaryExpression",
    "BooleanExpression",
    "CaseWhenBranch",
    "CaseWhenExpression",
    "CastExpression",
    "CoalesceExpression",
    "ColumnExpression",
    "CompareExpression",
    "DatePartExpression",
    "DateTruncExpression",
    "Expression",
    "ExpressionDataType",
    "LiteralExpression",
    "MAX_EXPRESSION_DEPTH",
    "MAX_EXPRESSION_NODES",
    "NullCheckExpression",
    "SetExpression",
    "StringTransformExpression",
    "UnaryExpression",
    "expression_children",
    "expression_column_keys",
    "map_expression_columns",
    "validate_expression_size",
    "walk_expression",
]
