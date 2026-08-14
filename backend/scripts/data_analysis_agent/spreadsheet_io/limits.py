"""Resource caps for spreadsheet conversion.

Conversion happens inside a request, so every loop over user-supplied content
needs a bound. These limits are deliberately stricter than Excel's own: the
goal is a workbook a browser grid can hold, not the largest file the format
allows.
"""

from __future__ import annotations

from dataclasses import dataclass


class SpreadsheetConversionError(ValueError):
    """Raised when a workbook cannot be converted safely or faithfully."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SpreadsheetLimits:
    """Caps applied while reading or writing a workbook."""

    max_sheets: int = 64
    max_rows_per_sheet: int = 100_000
    max_columns_per_sheet: int = 1_024
    #: Populated cells, not the addressable grid — a sparse sheet is cheap.
    max_cells_per_sheet: int = 400_000
    max_cells_total: int = 800_000
    max_merges_per_sheet: int = 10_000
    #: Excel's own per-cell text ceiling.
    max_string_length: int = 32_767
    max_formula_length: int = 8_192
    #: Interned style table size; beyond this a file is pathological.
    max_styles: int = 10_000

    def __post_init__(self) -> None:
        values = (
            self.max_sheets,
            self.max_rows_per_sheet,
            self.max_columns_per_sheet,
            self.max_cells_per_sheet,
            self.max_cells_total,
            self.max_merges_per_sheet,
            self.max_string_length,
            self.max_formula_length,
            self.max_styles,
        )
        if any(value <= 0 for value in values):
            raise ValueError("spreadsheet limits must be positive")


DEFAULT_LIMITS = SpreadsheetLimits()


def ensure(condition: bool, code: str, message: str) -> None:
    """Raise a coded conversion error when a limit is exceeded."""

    if not condition:
        raise SpreadsheetConversionError(code, message)
