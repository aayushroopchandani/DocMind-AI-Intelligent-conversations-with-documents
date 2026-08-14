"""CSV bytes to the interchange model.

Import has to accept what people actually have, and half of it is CSV. The
conversion is deliberately conservative: values are typed only when the whole
token is unambiguous, because a CSV that turns `007` into `7` or a product
code into a date has destroyed data rather than imported it.
"""

from __future__ import annotations

import csv
import io
from typing import Final

from scripts.data_analysis_agent.spreadsheet_io.limits import (
    DEFAULT_LIMITS,
    SpreadsheetConversionError,
    SpreadsheetLimits,
    ensure,
)
from scripts.data_analysis_agent.spreadsheet_io.workbook_model import (
    Cell,
    CellType,
    WorkbookDocument,
    Worksheet,
)


_SNIFF_BYTES: Final[int] = 16_384
_BOOLEANS: Final[dict[str, bool]] = {"true": True, "false": False}


def _decode(content: bytes) -> str:
    """UTF-8 first (with or without BOM), then a permissive fallback."""

    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SpreadsheetConversionError(
        "undecodable_csv", "This CSV file is not valid UTF-8 or Windows-1252 text."
    )


def _dialect(sample: str) -> type[csv.Dialect] | csv.Dialect:
    """Detect the delimiter; European exports are frequently semicolon-based."""

    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def _typed(token: str) -> tuple[CellType, str | float | bool | None]:
    text = token.strip()
    if not text:
        return CellType.BLANK, None

    lowered = text.casefold()
    if lowered in _BOOLEANS:
        return CellType.BOOLEAN, _BOOLEANS[lowered]

    # A leading zero or a plus sign means the text form is significant
    # (postcodes, phone numbers, product codes) — keep it as text.
    if text[0] == "+" or (len(text) > 1 and text[0] == "0" and text[1] != "."):
        return CellType.STRING, text

    try:
        number = float(text)
    except ValueError:
        return CellType.STRING, text
    if number != number or number in (float("inf"), float("-inf")):
        return CellType.STRING, text
    return CellType.NUMBER, number


def read_csv(
    content: bytes,
    *,
    name: str,
    sheet_name: str = "Sheet1",
    limits: SpreadsheetLimits = DEFAULT_LIMITS,
) -> WorkbookDocument:
    """Convert CSV bytes into a single-sheet workbook."""

    text = _decode(content)
    reader = csv.reader(io.StringIO(text, newline=""), _dialect(text[:_SNIFF_BYTES]))

    cells: list[Cell] = []
    row_count = 0
    column_count = 0

    for row_index, row in enumerate(reader):
        ensure(
            row_index < limits.max_rows_per_sheet,
            "rows_truncated",
            f"CSV has more than {limits.max_rows_per_sheet} rows.",
        )
        ensure(
            len(row) <= limits.max_columns_per_sheet,
            "columns_truncated",
            f"CSV has more than {limits.max_columns_per_sheet} columns.",
        )
        for column_index, token in enumerate(row):
            cell_type, value = _typed(token)
            if cell_type is CellType.BLANK:
                continue
            if isinstance(value, str) and len(value) > limits.max_string_length:
                value = value[: limits.max_string_length]
            cells.append(
                Cell(
                    row=row_index,
                    column=column_index,
                    value=value,
                    type=cell_type,
                )
            )
            ensure(
                len(cells) <= limits.max_cells_per_sheet,
                "sheet_too_large",
                f"CSV has more than {limits.max_cells_per_sheet} populated cells.",
            )
            column_count = max(column_count, column_index + 1)
        row_count = row_index + 1

    return WorkbookDocument(
        name=name,
        sheets=[
            Worksheet(
                name=sheet_name,
                index=0,
                row_count=row_count,
                column_count=column_count,
                cells=cells,
            )
        ],
    )
