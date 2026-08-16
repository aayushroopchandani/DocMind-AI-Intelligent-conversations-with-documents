"""What a generated formula may never contain (Phase 9.7.2).

Two different dangers live here and they need different defenses.

*Unsafe functions.* The formula AST is a closed union, so an unknown function
name cannot be represented in the first place — there is no `function_name`
field for a planner to fill in. The denylist below is therefore not the primary
defense; it exists so that a future contributor adding a node has an explicit,
reviewed list of what was ruled out and why, and so a compiled string can be
audited after the fact.

*Formula injection through data.* This one is real regardless of the AST. A cell
whose text begins with `=`, `+`, `-` or `@` is interpreted as a formula by every
major spreadsheet. Imported or generated text starting with those characters
must be written so it stays text, or a value like `=cmd|...` becomes executable
the moment the workbook opens.
"""

from __future__ import annotations

import re


FORMULA_COMPILER_VERSION = "1.0"
FORMULA_LOCALE = "en-US"
"""Phase 9 v1 emits en-US syntax: comma argument separators, `.` decimals.
A different locale fails loudly rather than emitting subtly wrong text."""

MAX_FORMULA_LENGTH = 8_192
MAX_FORMULA_DEPTH = 24
MAX_FORMULA_REFERENCES = 256


# Never emitted by the compiler. Recorded explicitly so the reasoning survives.
DENIED_FUNCTIONS: frozenset[str] = frozenset(
    {
        # Dynamic references defeat static analysis of what a formula touches.
        "INDIRECT",
        "OFFSET",
        # Volatile: the value changes on recalculation, so a hashed result is
        # not reproducible.
        "RAND",
        "RANDBETWEEN",
        "RANDARRAY",
        "NOW",
        "TODAY",
        # Network capable.
        "WEBSERVICE",
        "FILTERXML",
        "IMPORTDATA",
        "IMPORTXML",
        "IMPORTHTML",
        "IMPORTFEED",
        "IMPORTRANGE",
        # External workbook and system reach.
        "HYPERLINK",
        "CALL",
        "REGISTER.ID",
        "EXEC",
        "DDE",
    }
)

# The complete set the compiler can produce. Anything outside it is a bug in the
# compiler rather than untrusted input, but the check is cheap.
ALLOWED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "IF",
        "IFERROR",
        "AND",
        "OR",
        "NOT",
        "ABS",
        "ROUND",
        "SUM",
        "AVERAGE",
        "MIN",
        "MAX",
        "COUNT",
        "COUNTA",
        "YEAR",
        "MONTH",
        "DAY",
        "DATE",
        "LEN",
        "TRIM",
        "LOWER",
        "UPPER",
        "CONCAT",
    }
)

_FUNCTION_TOKEN = re.compile(r"([A-Za-z][A-Za-z0-9._]*)\s*\(")
_EXTERNAL_REFERENCE = re.compile(r"\[[^\]]+\]|'[^']*\.xlsx?'|https?://", re.IGNORECASE)

_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class FormulaSafetyError(ValueError):
    """A formula would be unsafe to place in a workbook."""


def is_injection_risk(value: object) -> bool:
    """Return whether a text value would be read as a formula by a spreadsheet."""

    return isinstance(value, str) and value.startswith(_INJECTION_PREFIXES)


def neutralize_text(value: str) -> str:
    """Return `value` so a spreadsheet keeps it as text.

    A leading apostrophe is the portable way to force text; it is not part of
    the stored value and is not displayed. Used for data written into cells,
    never for the formula the compiler itself produces.
    """

    return f"'{value}" if is_injection_risk(value) else value


def strip_string_literals(formula: str) -> str:
    """Return `formula` with the contents of quoted literals blanked out.

    The audit below scans for function calls and external references. Both are
    syntax, and neither can occur inside a quoted string — a literal containing
    the text `INDIRECT(` is a value, not a call. Scanning the raw text would
    reject that perfectly safe formula, so literals are removed first.

    A doubled quote is an escaped quote inside a literal, which is what keeps a
    crafted string from closing the literal and appending real formula text.
    """

    output: list[str] = []
    inside = False
    index = 0
    while index < len(formula):
        character = formula[index]
        if character == '"':
            if inside and formula[index : index + 2] == '""':
                index += 2
                continue
            inside = not inside
            output.append('"')
            index += 1
            continue
        if not inside:
            output.append(character)
        index += 1
    if inside:
        raise FormulaSafetyError("formula has an unterminated string literal")
    return "".join(output)


def audit_compiled_formula(formula: str) -> None:
    """Re-check a finished formula string before it can reach a workbook.

    The AST already makes an unsafe formula unrepresentable. This runs anyway,
    because the cost of being wrong here is arbitrary execution in the user's
    spreadsheet and the cost of the check is a regex.
    """

    if not formula.startswith("="):
        raise FormulaSafetyError("a compiled formula must begin with '='")
    if len(formula) > MAX_FORMULA_LENGTH:
        raise FormulaSafetyError(
            f"formula is {len(formula)} characters, above the "
            f"{MAX_FORMULA_LENGTH} limit"
        )
    syntax = strip_string_literals(formula)
    if _EXTERNAL_REFERENCE.search(syntax):
        raise FormulaSafetyError(
            "formula references an external workbook or URL"
        )
    for name in _FUNCTION_TOKEN.findall(syntax):
        upper = name.upper()
        if upper in DENIED_FUNCTIONS:
            raise FormulaSafetyError(f"formula uses the denied function '{upper}'")
        if upper not in ALLOWED_FUNCTIONS:
            raise FormulaSafetyError(f"formula uses the unknown function '{upper}'")


def require_supported_locale(locale: str) -> None:
    if locale != FORMULA_LOCALE:
        raise FormulaSafetyError(
            f"formula locale '{locale}' is not supported; Phase 9 emits "
            f"{FORMULA_LOCALE} syntax only"
        )


__all__ = [
    "ALLOWED_FUNCTIONS",
    "DENIED_FUNCTIONS",
    "FORMULA_COMPILER_VERSION",
    "FORMULA_LOCALE",
    "MAX_FORMULA_DEPTH",
    "MAX_FORMULA_LENGTH",
    "MAX_FORMULA_REFERENCES",
    "FormulaSafetyError",
    "audit_compiled_formula",
    "strip_string_literals",
    "is_injection_risk",
    "neutralize_text",
    "require_supported_locale",
]
