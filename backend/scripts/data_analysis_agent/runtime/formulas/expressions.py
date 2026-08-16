"""The typed semantic formula AST (Phase 9.7.1).

Deliberately separate from the native expression AST in `models/expressions.py`,
even though the two overlap. A native expression describes a value the engine
computes once; a formula describes a live cell that keeps recomputing inside the
user's workbook. They need different things:

* a formula must say what to show when a division fails or a cell errors, which
  a batch computation never has to answer;
* a formula can aggregate a whole column range, which is a different shape from
  a row-wise native expression;
* a formula's column references become coordinates, not column names.

Keeping them apart means neither has to carry the other's concerns. `native.py`
translates a formula to its native equivalent so a preview can be computed
server-side before any cell is written.

There is no `function_name` field anywhere in this union. A formula can only
name a function this module declares, so an unknown or unsafe function is
unrepresentable rather than merely rejected.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from .safety import MAX_FORMULA_DEPTH, MAX_FORMULA_REFERENCES


FORMULA_SCHEMA_VERSION = "1.0"


class FormulaValueType(str, Enum):
    NUMBER = "number"
    TEXT = "text"
    BOOLEAN = "boolean"
    DATE = "date"


class FormulaLiteral(BaseModel):
    kind: Literal["literal"] = "literal"
    value: JsonValue
    value_type: FormulaValueType

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("value")
    @classmethod
    def reject_non_finite(cls, value: JsonValue) -> JsonValue:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("formula literals must be finite")
        return value


class FormulaColumnRef(BaseModel):
    """A reference to the current row of a column, resolved after placement."""

    kind: Literal["column_ref"] = "column_ref"
    column_key: str = Field(min_length=1, max_length=120)

    model_config = ConfigDict(extra="forbid", frozen=True)


class FormulaArithmetic(BaseModel):
    kind: Literal["arithmetic"] = "arithmetic"
    operator: Literal["add", "subtract", "multiply"]
    left: FormulaExpression
    right: FormulaExpression

    model_config = ConfigDict(extra="forbid", frozen=True)


class FormulaSafeDivide(BaseModel):
    """Division with the two outcomes a live cell has to define."""

    kind: Literal["safe_divide"] = "safe_divide"
    numerator: FormulaExpression
    denominator: FormulaExpression
    on_zero: FormulaExpression
    on_error: FormulaExpression

    model_config = ConfigDict(extra="forbid", frozen=True)


class FormulaCompare(BaseModel):
    kind: Literal["compare"] = "compare"
    operator: Literal[
        "equal",
        "not_equal",
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
    ]
    left: FormulaExpression
    right: FormulaExpression

    model_config = ConfigDict(extra="forbid", frozen=True)


class FormulaBoolean(BaseModel):
    kind: Literal["boolean"] = "boolean"
    operator: Literal["and", "or"]
    operands: tuple[FormulaExpression, ...] = Field(min_length=2, max_length=16)

    model_config = ConfigDict(extra="forbid", frozen=True)


class FormulaNot(BaseModel):
    kind: Literal["not"] = "not"
    operand: FormulaExpression

    model_config = ConfigDict(extra="forbid", frozen=True)


class FormulaIf(BaseModel):
    kind: Literal["if"] = "if"
    condition: FormulaExpression
    then_value: FormulaExpression
    otherwise_value: FormulaExpression

    model_config = ConfigDict(extra="forbid", frozen=True)


class FormulaIfError(BaseModel):
    kind: Literal["if_error"] = "if_error"
    value: FormulaExpression
    fallback: FormulaExpression

    model_config = ConfigDict(extra="forbid", frozen=True)


class FormulaRound(BaseModel):
    kind: Literal["round"] = "round"
    value: FormulaExpression
    digits: int = Field(ge=0, le=12)

    model_config = ConfigDict(extra="forbid", frozen=True)


class FormulaAbsolute(BaseModel):
    kind: Literal["absolute"] = "absolute"
    value: FormulaExpression

    model_config = ConfigDict(extra="forbid", frozen=True)


class FormulaDatePart(BaseModel):
    kind: Literal["date_part"] = "date_part"
    part: Literal["year", "month", "day"]
    value: FormulaExpression

    model_config = ConfigDict(extra="forbid", frozen=True)


class FormulaTextTransform(BaseModel):
    kind: Literal["text_transform"] = "text_transform"
    operation: Literal["trim", "lower", "upper", "length"]
    value: FormulaExpression

    model_config = ConfigDict(extra="forbid", frozen=True)


class FormulaAggregate(BaseModel):
    """An aggregate over one column's whole data range.

    Bounded by construction: the range is the placed table's data rows, so a
    formula cannot reach outside the region the patch actually writes.
    """

    kind: Literal["aggregate"] = "aggregate"
    function: Literal["sum", "average", "min", "max", "count"]
    column_key: str = Field(min_length=1, max_length=120)

    model_config = ConfigDict(extra="forbid", frozen=True)


FormulaExpression = Annotated[
    FormulaLiteral
    | FormulaColumnRef
    | FormulaArithmetic
    | FormulaSafeDivide
    | FormulaCompare
    | FormulaBoolean
    | FormulaNot
    | FormulaIf
    | FormulaIfError
    | FormulaRound
    | FormulaAbsolute
    | FormulaDatePart
    | FormulaTextTransform
    | FormulaAggregate,
    Field(discriminator="kind"),
]


for _model in (
    FormulaArithmetic,
    FormulaSafeDivide,
    FormulaCompare,
    FormulaBoolean,
    FormulaNot,
    FormulaIf,
    FormulaIfError,
    FormulaRound,
    FormulaAbsolute,
    FormulaDatePart,
    FormulaTextTransform,
):
    _model.model_rebuild(_types_namespace={"FormulaExpression": FormulaExpression})


class FormulaSpec(BaseModel):
    """One semantic formula bound to an output column."""

    schema_version: Literal[FORMULA_SCHEMA_VERSION] = FORMULA_SCHEMA_VERSION
    output_column_key: str = Field(min_length=1, max_length=120)
    expression: FormulaExpression
    fill: Literal["down", "none"] = "down"
    # Applied to the cell separately from the value, per 9.7.3. A format never
    # changes what a formula computes.
    number_format: str | None = Field(default=None, max_length=60)

    model_config = ConfigDict(extra="forbid", frozen=True)


def formula_children(expression: FormulaExpression) -> tuple[FormulaExpression, ...]:
    if isinstance(expression, (FormulaArithmetic, FormulaCompare)):
        return (expression.left, expression.right)
    if isinstance(expression, FormulaSafeDivide):
        return (
            expression.numerator,
            expression.denominator,
            expression.on_zero,
            expression.on_error,
        )
    if isinstance(expression, FormulaBoolean):
        return expression.operands
    if isinstance(expression, FormulaNot):
        return (expression.operand,)
    if isinstance(expression, FormulaIf):
        return (
            expression.condition,
            expression.then_value,
            expression.otherwise_value,
        )
    if isinstance(expression, FormulaIfError):
        return (expression.value, expression.fallback)
    if isinstance(
        expression,
        (
            FormulaRound,
            FormulaAbsolute,
            FormulaDatePart,
            FormulaTextTransform,
        ),
    ):
        return (expression.value,)
    return ()


def walk_formula(
    expression: FormulaExpression,
) -> Iterator[tuple[FormulaExpression, int]]:
    stack: list[tuple[FormulaExpression, int]] = [(expression, 1)]
    while stack:
        current, depth = stack.pop()
        yield current, depth
        stack.extend(
            (child, depth + 1) for child in reversed(formula_children(current))
        )


def formula_column_keys(expression: FormulaExpression) -> tuple[str, ...]:
    """Return every column the formula reads, in first-seen order."""

    keys: list[str] = []
    for node, _depth in walk_formula(expression):
        if isinstance(node, (FormulaColumnRef, FormulaAggregate)):
            keys.append(node.column_key)
    return tuple(dict.fromkeys(keys))


def validate_formula_size(expression: FormulaExpression) -> None:
    references = 0
    for node, depth in walk_formula(expression):
        if depth > MAX_FORMULA_DEPTH:
            raise ValueError(f"formula depth cannot exceed {MAX_FORMULA_DEPTH}")
        if isinstance(node, (FormulaColumnRef, FormulaAggregate)):
            references += 1
            if references > MAX_FORMULA_REFERENCES:
                raise ValueError(
                    f"formula cannot reference more than "
                    f"{MAX_FORMULA_REFERENCES} cells"
                )


__all__ = [
    "FORMULA_SCHEMA_VERSION",
    "FormulaAbsolute",
    "FormulaAggregate",
    "FormulaArithmetic",
    "FormulaBoolean",
    "FormulaColumnRef",
    "FormulaCompare",
    "FormulaDatePart",
    "FormulaExpression",
    "FormulaIf",
    "FormulaIfError",
    "FormulaLiteral",
    "FormulaNot",
    "FormulaRound",
    "FormulaSafeDivide",
    "FormulaSpec",
    "FormulaTextTransform",
    "FormulaValueType",
    "formula_children",
    "formula_column_keys",
    "validate_formula_size",
    "walk_formula",
]
