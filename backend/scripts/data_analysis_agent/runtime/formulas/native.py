"""Translate a semantic formula into its native expression equivalent.

Phase 9.7.3 requires evaluating representative rows with the native semantics
*before* a patch is proposed. That is only meaningful if the two agree, so the
translation lives here as one explicit function rather than being reimplemented
by whoever needs a preview.

Two node kinds have no row-wise native equivalent and are rejected rather than
approximated:

* `aggregate`, which folds a whole column and so is not a per-row value;
* `if_error`, whose fallback exists for spreadsheet error values (`#VALUE!`,
  `#REF!`) that the native engine does not produce at all.

A formula containing either can still be compiled and placed; it just cannot be
previewed row-by-row, and the caller is told so instead of being handed a
silently different answer.
"""

from __future__ import annotations

from ..models.expressions import (
    BinaryExpression,
    BooleanExpression,
    CaseWhenBranch,
    CaseWhenExpression,
    ColumnExpression,
    CompareExpression,
    DatePartExpression,
    Expression,
    ExpressionDataType,
    LiteralExpression,
    StringTransformExpression,
    UnaryExpression,
)
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
    FormulaTextTransform,
    FormulaValueType,
)


class FormulaNotPreviewableError(ValueError):
    """The formula has no row-wise native equivalent."""


_LITERAL_TYPES = {
    FormulaValueType.NUMBER: ExpressionDataType.NUMBER,
    FormulaValueType.TEXT: ExpressionDataType.STRING,
    FormulaValueType.BOOLEAN: ExpressionDataType.BOOLEAN,
    FormulaValueType.DATE: ExpressionDataType.DATE,
}

_TEXT_TRANSFORMS = {
    "trim": "trim",
    "lower": "lowercase",
    "upper": "uppercase",
    "length": "length",
}


def is_previewable(expression: FormulaExpression) -> bool:
    """Return whether every node has a row-wise native equivalent."""

    try:
        to_native_expression(expression)
    except FormulaNotPreviewableError:
        return False
    return True


def to_native_expression(expression: FormulaExpression) -> Expression:
    """Return the native expression computing the same per-row value."""

    if isinstance(expression, FormulaColumnRef):
        return ColumnExpression(column_key=expression.column_key)

    if isinstance(expression, FormulaLiteral):
        return LiteralExpression(
            value=expression.value,
            data_type=_LITERAL_TYPES[expression.value_type],
        )

    if isinstance(expression, FormulaArithmetic):
        return BinaryExpression(
            operator=expression.operator,
            left=to_native_expression(expression.left),
            right=to_native_expression(expression.right),
        )

    if isinstance(expression, FormulaSafeDivide):
        # The sheet's `on_zero` is exactly the native "null" policy wrapped in a
        # case: divide, and substitute the declared value when the divisor is 0.
        quotient = BinaryExpression(
            operator="safe_divide",
            left=to_native_expression(expression.numerator),
            right=to_native_expression(expression.denominator),
            zero_division="null",
        )
        is_zero = CompareExpression(
            operator="equal",
            left=to_native_expression(expression.denominator),
            right=LiteralExpression(value=0, data_type=ExpressionDataType.NUMBER),
        )
        return CaseWhenExpression(
            branches=(
                CaseWhenBranch(
                    condition=is_zero,
                    result=to_native_expression(expression.on_zero),
                ),
            ),
            otherwise=quotient,
        )

    if isinstance(expression, FormulaCompare):
        return CompareExpression(
            operator=expression.operator,
            left=to_native_expression(expression.left),
            right=to_native_expression(expression.right),
        )

    if isinstance(expression, FormulaBoolean):
        return BooleanExpression(
            operator=expression.operator,
            operands=tuple(
                to_native_expression(item) for item in expression.operands
            ),
        )

    if isinstance(expression, FormulaNot):
        return UnaryExpression(
            operator="not",
            operand=to_native_expression(expression.operand),
        )

    if isinstance(expression, FormulaIf):
        return CaseWhenExpression(
            branches=(
                CaseWhenBranch(
                    condition=to_native_expression(expression.condition),
                    result=to_native_expression(expression.then_value),
                ),
            ),
            otherwise=to_native_expression(expression.otherwise_value),
        )

    if isinstance(expression, FormulaAbsolute):
        return UnaryExpression(
            operator="absolute",
            operand=to_native_expression(expression.value),
        )

    if isinstance(expression, FormulaDatePart):
        return DatePartExpression(
            part=expression.part,
            expression=to_native_expression(expression.value),
        )

    if isinstance(expression, FormulaTextTransform):
        return StringTransformExpression(
            operation=_TEXT_TRANSFORMS[expression.operation],
            expression=to_native_expression(expression.value),
        )

    if isinstance(expression, FormulaRound):
        # Rounding is a property of the derived column, not of the expression
        # tree, so the caller applies `rounding_scale` to the step instead.
        return to_native_expression(expression.value)

    if isinstance(expression, FormulaAggregate):
        raise FormulaNotPreviewableError(
            f"'{expression.function}' folds a whole column and has no row-wise "
            "native equivalent"
        )

    if isinstance(expression, FormulaIfError):
        raise FormulaNotPreviewableError(
            "IFERROR handles spreadsheet error values, which the native engine "
            "never produces"
        )

    raise FormulaNotPreviewableError(
        f"unsupported formula node: {type(expression).__name__}"
    )


def rounding_scale(expression: FormulaExpression) -> int | None:
    """Return the outermost ROUND's digits, which become the column's scale."""

    return expression.digits if isinstance(expression, FormulaRound) else None


__all__ = [
    "FormulaNotPreviewableError",
    "is_previewable",
    "rounding_scale",
    "to_native_expression",
]
