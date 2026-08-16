"""Compile the typed expression AST into Polars expressions.

This is the only place where plan content becomes engine behaviour, so it is
written as a total function over the closed union: every node kind is handled
explicitly and an unknown node raises rather than falling through. There is no
string evaluation, no attribute lookup driven by plan content, and no function
registry keyed by a name the planner chose — a node can only reach an operator
that this module names literally.

The AST has already been schema-, type- and unit-checked by
`planning/expression_validation.py`. This module trusts those guarantees for
typing and re-checks only what affects runtime safety: column existence and
division-by-zero policy.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from ...models.expressions import (
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
    expression_children,
)


class ExpressionCompilationError(ValueError):
    """An expression cannot be compiled into a safe native expression."""


_EXPRESSION_DTYPE = {
    ExpressionDataType.STRING: pl.String,
    ExpressionDataType.INTEGER: pl.Int64,
    ExpressionDataType.NUMBER: pl.Float64,
    ExpressionDataType.DECIMAL: pl.Float64,
    ExpressionDataType.CURRENCY: pl.Float64,
    ExpressionDataType.PERCENTAGE: pl.Float64,
    ExpressionDataType.BOOLEAN: pl.Boolean,
    ExpressionDataType.DATE: pl.Date,
    ExpressionDataType.PERIOD: pl.String,
}

_DATE_PART = {
    "year": lambda expression: expression.dt.year(),
    "quarter": lambda expression: expression.dt.quarter(),
    "month": lambda expression: expression.dt.month(),
    "day": lambda expression: expression.dt.day(),
    "week": lambda expression: expression.dt.week(),
    "weekday": lambda expression: expression.dt.weekday(),
}

_TRUNC_INTERVAL = {
    "year": "1y",
    "quarter": "1q",
    "month": "1mo",
    "week": "1w",
    "day": "1d",
}

_STRING_TRANSFORM = {
    "trim": lambda expression: expression.str.strip_chars(),
    "lowercase": lambda expression: expression.str.to_lowercase(),
    "uppercase": lambda expression: expression.str.to_uppercase(),
    "length": lambda expression: expression.str.len_chars().cast(pl.Int64),
}


def compile_expression(
    expression: Expression,
    *,
    available_columns: frozenset[str],
) -> pl.Expr:
    """Return the Polars expression for one validated AST node."""

    if isinstance(expression, ColumnExpression):
        if expression.column_key not in available_columns:
            raise ExpressionCompilationError(
                f"expression references unavailable column "
                f"'{expression.column_key}'"
            )
        return pl.col(expression.column_key)

    if isinstance(expression, LiteralExpression):
        return _literal(expression)

    def child(node: Expression) -> pl.Expr:
        return compile_expression(node, available_columns=available_columns)

    if isinstance(expression, UnaryExpression):
        operand = child(expression.operand)
        if expression.operator == "negate":
            return -operand
        if expression.operator == "absolute":
            return operand.abs()
        return ~operand

    if isinstance(expression, BinaryExpression):
        return _binary(expression, child(expression.left), child(expression.right))

    if isinstance(expression, CompareExpression):
        return _compare(expression, child(expression.left), child(expression.right))

    if isinstance(expression, SetExpression):
        values = [_literal(item) for item in expression.values]
        membership = child(expression.expression).is_in(pl.concat_list(values))
        return membership if expression.operator == "in" else ~membership

    if isinstance(expression, BetweenExpression):
        subject = child(expression.expression)
        lower = child(expression.lower)
        upper = child(expression.upper)
        if expression.inclusive:
            return (subject >= lower) & (subject <= upper)
        return (subject > lower) & (subject < upper)

    if isinstance(expression, BooleanExpression):
        operands = [child(item) for item in expression.operands]
        combined = operands[0]
        for operand in operands[1:]:
            combined = (
                combined & operand
                if expression.operator == "and"
                else combined | operand
            )
        return combined

    if isinstance(expression, CaseWhenExpression):
        branches = list(expression.branches)
        chain = pl.when(child(branches[0].condition)).then(child(branches[0].result))
        for branch in branches[1:]:
            chain = chain.when(child(branch.condition)).then(child(branch.result))
        return chain.otherwise(child(expression.otherwise))

    if isinstance(expression, CoalesceExpression):
        return pl.coalesce([child(item) for item in expression.expressions])

    if isinstance(expression, CastExpression):
        return child(expression.expression).cast(
            polars_dtype_for(expression.target_type),
            strict=expression.failure_policy == "error",
        )

    if isinstance(expression, DatePartExpression):
        return _DATE_PART[expression.part](child(expression.expression)).cast(pl.Int64)

    if isinstance(expression, DateTruncExpression):
        return child(expression.expression).dt.truncate(
            _TRUNC_INTERVAL[expression.granularity]
        )

    if isinstance(expression, StringTransformExpression):
        return _STRING_TRANSFORM[expression.operation](child(expression.expression))

    if isinstance(expression, NullCheckExpression):
        subject = child(expression.expression)
        return (
            subject.is_null()
            if expression.operator == "is_null"
            else subject.is_not_null()
        )

    raise ExpressionCompilationError(
        f"unsupported expression node: {type(expression).__name__}"
    )


def polars_dtype_for(data_type: ExpressionDataType) -> pl.DataType:
    try:
        return _EXPRESSION_DTYPE[data_type]
    except KeyError:
        raise ExpressionCompilationError(
            f"literal type '{data_type.value}' has no native representation"
        ) from None


def _literal(expression: LiteralExpression) -> pl.Expr:
    dtype = polars_dtype_for(expression.data_type)
    value = expression.value
    if value is None:
        return pl.lit(None, dtype=dtype)
    if expression.data_type is ExpressionDataType.DATE and isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            raise ExpressionCompilationError(
                "date literals must be ISO calendar dates"
            ) from None
    return pl.lit(value, dtype=dtype)


def _binary(
    expression: BinaryExpression,
    left: pl.Expr,
    right: pl.Expr,
) -> pl.Expr:
    operator = expression.operator
    if operator == "add":
        return left + right
    if operator == "subtract":
        return left - right
    if operator == "multiply":
        return left * right
    if operator == "modulo":
        # Guard the divisor so a zero modulus yields null instead of an engine
        # level error that would abort the whole stage.
        return pl.when(right == 0).then(None).otherwise(left % right)
    if operator == "safe_divide":
        return _safe_divide(expression, left, right)
    raise ExpressionCompilationError(f"unsupported arithmetic operator '{operator}'")


def _safe_divide(
    expression: BinaryExpression,
    left: pl.Expr,
    right: pl.Expr,
) -> pl.Expr:
    policy = expression.zero_division
    quotient = left / right
    if policy == "null":
        return pl.when(right == 0).then(None).otherwise(quotient)
    if policy == "zero":
        return pl.when(right == 0).then(pl.lit(0.0, dtype=pl.Float64)).otherwise(
            quotient
        )
    if policy == "error":
        # There is no way to raise from inside a Polars expression, so the
        # divisor is checked as a stage precondition instead. See
        # `operation_compiler.zero_division_guards`.
        return quotient
    raise ExpressionCompilationError(
        "safe_divide requires an explicit zero_division policy"
    )


def _compare(
    expression: CompareExpression,
    left: pl.Expr,
    right: pl.Expr,
) -> pl.Expr:
    operator = expression.operator
    if operator == "equal":
        return left == right
    if operator == "not_equal":
        return left != right
    if operator == "greater_than":
        return left > right
    if operator == "greater_than_or_equal":
        return left >= right
    if operator == "less_than":
        return left < right
    if operator == "less_than_or_equal":
        return left <= right
    if operator == "contains":
        # `literal=True` keeps the operand a plain substring; without it the
        # planner's text would be compiled as a regular expression.
        return left.str.contains(right, literal=True)
    if operator == "starts_with":
        return left.str.starts_with(right)
    if operator == "ends_with":
        return left.str.ends_with(right)
    raise ExpressionCompilationError(f"unsupported comparison operator '{operator}'")


def strict_zero_divisors(expression: Expression) -> tuple[Expression, ...]:
    """Return every divisor whose plan demands an error on division by zero."""

    if isinstance(expression, BinaryExpression):
        found: list[Expression] = []
        if expression.operator == "safe_divide" and expression.zero_division == "error":
            found.append(expression.right)
        found.extend(strict_zero_divisors(expression.left))
        found.extend(strict_zero_divisors(expression.right))
        return tuple(found)
    return tuple(
        divisor
        for child in expression_children(expression)
        for divisor in strict_zero_divisors(child)
    )


__all__ = [
    "ExpressionCompilationError",
    "compile_expression",
    "polars_dtype_for",
    "strict_zero_divisors",
]
