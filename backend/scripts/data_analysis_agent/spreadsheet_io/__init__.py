"""Spreadsheet import and export for the data-analysis workspace.

A self-contained, dependency-light converter between `.xlsx` files and an
engine-neutral workbook model. It knows nothing about the Phase 8/9 analysis
runtime, MongoDB or Cloudinary, and nothing about the frontend's grid — it is
pure bytes-to-model and model-to-bytes, which keeps it testable and reusable
by the agent later.
"""

from scripts.data_analysis_agent.spreadsheet_io.limits import (
    DEFAULT_LIMITS,
    SpreadsheetConversionError,
    SpreadsheetLimits,
)
from scripts.data_analysis_agent.spreadsheet_io.workbook_model import (
    SCHEMA_VERSION,
    BorderEdge,
    BorderStyle,
    Cell,
    CellStyle,
    CellType,
    ColumnMeta,
    ConversionWarning,
    DateSystem,
    HorizontalAlignment,
    RowMeta,
    VerticalAlignment,
    WorkbookDocument,
    Worksheet,
)
from scripts.data_analysis_agent.spreadsheet_io.xlsx_reader import read_xlsx
from scripts.data_analysis_agent.spreadsheet_io.xlsx_writer import write_xlsx


__all__ = [
    "DEFAULT_LIMITS",
    "SCHEMA_VERSION",
    "BorderEdge",
    "BorderStyle",
    "Cell",
    "CellStyle",
    "CellType",
    "ColumnMeta",
    "ConversionWarning",
    "DateSystem",
    "HorizontalAlignment",
    "RowMeta",
    "SpreadsheetConversionError",
    "SpreadsheetLimits",
    "VerticalAlignment",
    "WorkbookDocument",
    "Worksheet",
    "read_xlsx",
    "write_xlsx",
]
