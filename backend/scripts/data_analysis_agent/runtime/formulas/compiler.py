"""Compile a semantic formula into en-US A1 text (Phase 9.7.3).

Column keys become coordinates here and nowhere earlier. That is the whole point
of 9.7's indirection: the plan talks about `revenue`, and only once placement is
decided does `revenue` become column `D`. A column that moves or is renamed
changes its placement entry, not the formula, so a rename can never silently
retarget a formula at the wrong data.

The compiler emits a *seed* formula for the first data row using relative
references. Filling it down produces the remaining rows, which is both what a
spreadsheet user expects and far smaller than a per-row formula matrix.
Aggregates use absolute row anchors so filling does not slide the range.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

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
from .safety import (
    FORMULA_COMPILER_VERSION,
    FORMULA_LOCALE,
    FormulaSafetyError,
    audit_compiled_formula,
    require_supported_locale,
)


class FormulaCompilationError(ValueError):
    """A formula cannot be compiled for the given placement."""


@dataclass(frozen=True, slots=True)
class FormulaPlacement:
    """Where the formula's columns actually landed in the sheet.

    Supplied by the placement step, never guessed by the planner.
    """

    columns: dict[str, str]
    """Stable column key to spreadsheet column letter, e.g. `{"revenue": "D"}`."""
    first_data_row: int
    last_data_row: int

    def __post_init__(self) -> None:
        if self.first_data_row < 1:
            raise ValueError("first_data_row must be a positive spreadsheet row")
        if self.last_data_row < self.first_data_row:
            raise ValueError("last_data_row cannot precede first_data_row")

    def letter(self, column_key: str) -> str:
        try:
            return self.columns[column_key]
        except KeyError:
            raise FormulaCompilationError(
                f"formula references column '{column_key}', which was not placed"
            ) from None


@dataclass(frozen=True, slots=True)
class CompiledFormula:
    """The seed formula plus everything lineage needs to explain it."""

    formula: str
    compiler_version: str
    locale: str
    seed_row: int
    fill_through_row: int
    number_format: str | None
    coordinate_map: dict[str, str]
    """Column key to the coordinate the seed formula used, for lineage (9.7.3)."""

    @property
    def fill_row_count(self) -> int:
        return self.fill_through_row - self.seed_row + 1


_COMPARISONS = {
    "equal": "=",
    "not_equal": "<>",
    "greater_than": ">",
    "greater_than_or_equal": ">=",
    "less_than": "<",
    "less_than_or_equal": "<=",
}

_ARITHMETIC = {"add": "+", "subtract": "-", "multiply": "*"}

_DATE_PARTS = {"year": "YEAR", "month": "MONTH", "day": "DAY"}

_TEXT_TRANSFORMS = {
    "trim": "TRIM",
    "lower": "LOWER",
    "upper": "UPPER",
    "length": "LEN",
}

_AGGREGATES = {
    "sum": "SUM",
    "average": "AVERAGE",
    "min": "MIN",
    "max": "MAX",
    "count": "COUNT",
}


def compile_formula(
    spec: FormulaSpec,
    placement: FormulaPlacement,
    *,
    locale: str = FORMULA_LOCALE,
) -> CompiledFormula:
    """Return the seed formula for `spec` under `placement`."""

    require_supported_locale(locale)
    validate_formula_size(spec.expression)

    row = placement.first_data_row
    body = _compile(spec.expression, placement, row)
    formula = f"={body}"
    audit_compiled_formula(formula)

    return CompiledFormula(
        formula=formula,
        compiler_version=FORMULA_COMPILER_VERSION,
        locale=locale,
        seed_row=row,
        fill_through_row=(
            placement.last_data_row if spec.fill == "down" else row
        ),
        number_format=spec.number_format,
        coordinate_map={
            key: f"{placement.letter(key)}{row}"
            for key in placement.columns
        },
    )


def _compile(
    expression: FormulaExpression,
    placement: FormulaPlacement,
    row: int,
) -> str:
    def child(node: FormulaExpression) -> str:
        return _compile(node, placement, row)

    if isinstance(expression, FormulaColumnRef):
        # Relative reference: filling down moves it to the next row.
        return f"{placement.letter(expression.column_key)}{row}"

    if isinstance(expression, FormulaLiteral):
        return _literal(expression)

    if isinstance(expression, FormulaArithmetic):
        operator = _ARITHMETIC[expression.operator]
        return f"({child(expression.left)}{operator}{child(expression.right)})"

    if isinstance(expression, FormulaSafeDivide):
        numerator = child(expression.numerator)
        denominator = child(expression.denominator)
        # IF guards the declared zero case; IFERROR catches everything else the
        # sheet might raise, so the cell never shows #DIV/0! or #VALUE!.
        return (
            f"IFERROR(IF({denominator}=0,{child(expression.on_zero)},"
            f"{numerator}/{denominator}),{child(expression.on_error)})"
        )

    if isinstance(expression, FormulaCompare):
        operator = _COMPARISONS[expression.operator]
        return f"({child(expression.left)}{operator}{child(expression.right)})"

    if isinstance(expression, FormulaBoolean):
        function = "AND" if expression.operator == "and" else "OR"
        arguments = ",".join(child(item) for item in expression.operands)
        return f"{function}({arguments})"

    if isinstance(expression, FormulaNot):
        return f"NOT({child(expression.operand)})"

    if isinstance(expression, FormulaIf):
        return (
            f"IF({child(expression.condition)},"
            f"{child(expression.then_value)},"
            f"{child(expression.otherwise_value)})"
        )

    if isinstance(expression, FormulaIfError):
        return f"IFERROR({child(expression.value)},{child(expression.fallback)})"

    if isinstance(expression, FormulaRound):
        return f"ROUND({child(expression.value)},{expression.digits})"

    if isinstance(expression, FormulaAbsolute):
        return f"ABS({child(expression.value)})"

    if isinstance(expression, FormulaDatePart):
        return f"{_DATE_PARTS[expression.part]}({child(expression.value)})"

    if isinstance(expression, FormulaTextTransform):
        return f"{_TEXT_TRANSFORMS[expression.operation]}({child(expression.value)})"

    if isinstance(expression, FormulaAggregate):
        letter = placement.letter(expression.column_key)
        # Absolute rows so filling the seed down does not slide the range.
        span = (
            f"{letter}${placement.first_data_row}:"
            f"{letter}${placement.last_data_row}"
        )
        return f"{_AGGREGATES[expression.function]}({span})"

    raise FormulaCompilationError(
        f"unsupported formula node: {type(expression).__name__}"
    )


def _literal(expression: FormulaLiteral) -> str:
    value = expression.value
    if value is None:
        return '""'
    if expression.value_type is FormulaValueType.BOOLEAN:
        return "TRUE" if value else "FALSE"
    if expression.value_type is FormulaValueType.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FormulaCompilationError("numeric literal is not a number")
        return repr(value)
    if expression.value_type is FormulaValueType.DATE:
        parsed = value if isinstance(value, date) else _parse_date(value)
        return f"DATE({parsed.year},{parsed.month},{parsed.day})"
    if not isinstance(value, str):
        raise FormulaCompilationError("text literal is not a string")
    return _quoted(value)


def _parse_date(value: object) -> date:
    if not isinstance(value, str):
        raise FormulaCompilationError("date literal must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise FormulaCompilationError(
            "date literal must be an ISO calendar date"
        ) from None


def _quoted(value: str) -> str:
    """Return a quoted string literal, escaping embedded quotes.

    A quote is doubled rather than backslash-escaped: that is the spreadsheet
    convention, and getting it wrong would let a crafted string close the
    literal and append arbitrary formula text.
    """

    if "\x00" in value:
        raise FormulaSafetyError("formula text cannot contain a null byte")
    return '"' + value.replace('"', '""') + '"'


__all__ = [
    "CompiledFormula",
    "FormulaCompilationError",
    "FormulaPlacement",
    "compile_formula",
]
