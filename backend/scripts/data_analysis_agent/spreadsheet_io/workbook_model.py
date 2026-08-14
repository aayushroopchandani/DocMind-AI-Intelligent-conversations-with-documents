"""Engine-neutral workbook interchange model.

This is the contract between the XLSX converter and any client. It is
deliberately *not* Univer's `IWorkbookData`: the backend should not encode a
particular frontend grid's internal shape, and the analysis agent wants to
read imported spreadsheets without knowing about Univer at all. The frontend
owns the small mapping from this model into its own workbook format.

Two shapes matter for size:

* cells are **sparse** — only populated cells are carried;
* styles are **interned** — cells reference a shared table by index, so a
  10,000-row report with one header style costs one style entry.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = 1


class CellType(str, Enum):
    """Matches the frontend's `WorkbookCellType`."""

    BLANK = "blank"
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    FORMULA = "formula"
    ERROR = "error"


class HorizontalAlignment(str, Enum):
    GENERAL = "general"
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class VerticalAlignment(str, Enum):
    TOP = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"


class BorderStyle(str, Enum):
    NONE = "none"
    THIN = "thin"
    MEDIUM = "medium"
    THICK = "thick"
    DASHED = "dashed"
    DOTTED = "dotted"
    DOUBLE = "double"


class DateSystem(str, Enum):
    """Excel's two epochs. Getting this wrong shifts every date by four years."""

    EXCEL_1900 = "1900"
    EXCEL_1904 = "1904"


class BorderEdge(BaseModel):
    style: BorderStyle
    color: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class CellStyle(BaseModel):
    """A resolved, absolute style — no theme references, no indexed palettes.

    Frozen so styles can be interned in a dict keyed by the model itself.
    """

    font_family: str | None = None
    font_size: float | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    #: `#RRGGBB`, already resolved from theme/indexed colours.
    text_color: str | None = None
    background_color: str | None = None
    horizontal: HorizontalAlignment | None = None
    vertical: VerticalAlignment | None = None
    wrap_text: bool = False
    #: Excel number-format pattern, verbatim (`General`, `#,##0.00`, …).
    number_format: str | None = None
    border_top: BorderEdge | None = None
    border_bottom: BorderEdge | None = None
    border_left: BorderEdge | None = None
    border_right: BorderEdge | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    def is_default(self) -> bool:
        """True when the style carries nothing worth storing."""

        return self == _DEFAULT_STYLE


_DEFAULT_STYLE = CellStyle()


class Cell(BaseModel):
    """One populated cell. Row and column are zero-based."""

    row: int = Field(ge=0)
    column: int = Field(ge=0)
    #: Dates arrive as their Excel serial number; `type` says how to read it.
    value: str | float | bool | None = None
    #: Formula text including the leading `=`.
    formula: str | None = None
    type: CellType = CellType.BLANK
    #: Index into `WorkbookDocument.styles`.
    style_id: int | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class ColumnMeta(BaseModel):
    index: int = Field(ge=0)
    #: Excel's character-width unit, not pixels.
    width: float | None = None
    hidden: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)


class RowMeta(BaseModel):
    index: int = Field(ge=0)
    #: Points, as Excel stores row heights.
    height: float | None = None
    hidden: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)


class Worksheet(BaseModel):
    name: str
    index: int = Field(ge=0)
    hidden: bool = False
    tab_color: str | None = None
    #: Extent of the used range, so a client can size its grid.
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    cells: list[Cell] = Field(default_factory=list)
    #: A1 ranges, e.g. `A1:C3`.
    merges: list[str] = Field(default_factory=list)
    columns: list[ColumnMeta] = Field(default_factory=list)
    rows: list[RowMeta] = Field(default_factory=list)
    frozen_rows: int = Field(default=0, ge=0)
    frozen_columns: int = Field(default=0, ge=0)
    show_gridlines: bool = True

    model_config = ConfigDict(extra="forbid")


class ConversionWarning(BaseModel):
    """Something the converter could not carry across, named honestly."""

    code: str
    message: str
    #: Sheet the warning came from, when it is sheet-specific.
    sheet: str | None = None
    count: int = 1

    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkbookDocument(BaseModel):
    """A whole workbook, ready to hand to a grid."""

    schema_version: int = SCHEMA_VERSION
    name: str
    date_system: DateSystem = DateSystem.EXCEL_1900
    sheets: list[Worksheet] = Field(default_factory=list)
    #: Positional style table; `Cell.style_id` indexes into it.
    styles: list[CellStyle] = Field(default_factory=list)
    warnings: list[ConversionWarning] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @property
    def cell_count(self) -> int:
        return sum(len(sheet.cells) for sheet in self.sheets)


class StyleTable:
    """Interns styles so repeated formatting costs one entry.

    Reading a real report calls this once per cell, so the fast path is a
    single dict lookup on a frozen, hashable model.
    """

    __slots__ = ("_indices", "_styles", "_limit")

    def __init__(self, limit: int) -> None:
        self._indices: dict[CellStyle, int] = {}
        self._styles: list[CellStyle] = []
        self._limit = limit

    def intern(self, style: CellStyle) -> int | None:
        """Return the shared index for a style, or None if it is the default."""

        if style.is_default():
            return None
        existing = self._indices.get(style)
        if existing is not None:
            return existing
        if len(self._styles) >= self._limit:
            from scripts.data_analysis_agent.spreadsheet_io.limits import (
                SpreadsheetConversionError,
            )

            raise SpreadsheetConversionError(
                "too_many_styles",
                f"Workbook uses more than {self._limit} distinct cell styles.",
            )
        index = len(self._styles)
        self._styles.append(style)
        self._indices[style] = index
        return index

    def as_list(self) -> list[CellStyle]:
        return list(self._styles)
