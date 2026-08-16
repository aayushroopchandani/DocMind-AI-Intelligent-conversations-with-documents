"""Semantic spreadsheet formulas and their compiler (Phase 9.7).

The LLM proposes a typed formula tree, never formula text. This package turns
that tree into an en-US A1 formula once placement is known, validates it against
the schema it will run over, and can translate it to a native expression so a
preview is computed server-side before any cell is written.

Not wired into the plan contract yet, on purpose. The thing that would consume a
compiled formula is the workbook patch protocol, which does not exist; adding an
unreachable field to the plan schema now would repeat the "layer with no
consumer" problem that Phase 9.1 already had once.
"""

from .compiler import (
    CompiledFormula,
    FormulaCompilationError,
    FormulaPlacement,
    compile_formula,
)
from .expressions import (
    FORMULA_SCHEMA_VERSION,
    FormulaExpression,
    FormulaSpec,
    FormulaValueType,
    formula_column_keys,
)
from .native import (
    FormulaNotPreviewableError,
    is_previewable,
    rounding_scale,
    to_native_expression,
)
from .safety import (
    ALLOWED_FUNCTIONS,
    DENIED_FUNCTIONS,
    FORMULA_COMPILER_VERSION,
    FORMULA_LOCALE,
    FormulaSafetyError,
    audit_compiled_formula,
    is_injection_risk,
    neutralize_text,
)
from .validation import FormulaIssue, validate_formula

__all__ = [
    "ALLOWED_FUNCTIONS",
    "DENIED_FUNCTIONS",
    "FORMULA_COMPILER_VERSION",
    "FORMULA_LOCALE",
    "FORMULA_SCHEMA_VERSION",
    "CompiledFormula",
    "FormulaCompilationError",
    "FormulaExpression",
    "FormulaIssue",
    "FormulaNotPreviewableError",
    "FormulaPlacement",
    "FormulaSafetyError",
    "FormulaSpec",
    "FormulaValueType",
    "audit_compiled_formula",
    "compile_formula",
    "formula_column_keys",
    "is_injection_risk",
    "is_previewable",
    "neutralize_text",
    "rounding_scale",
    "to_native_expression",
    "validate_formula",
]
