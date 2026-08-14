"""Translation between openpyxl styling and the interchange model.

Colour is the only genuinely hard part. A cell colour in XLSX can be stated
three ways — a literal ARGB value, an index into a legacy palette, or a theme
slot plus a lightness tint — and only the first is portable. Everything is
resolved to `#RRGGBB` here so that no client has to carry a copy of Excel's
colour model.
"""

from __future__ import annotations

import colorsys
from typing import Any, Final

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.styles.colors import COLOR_INDEX, Color

from scripts.data_analysis_agent.spreadsheet_io.workbook_model import (
    BorderEdge,
    BorderStyle,
    CellStyle,
    HorizontalAlignment,
    VerticalAlignment,
)


# Office's default theme, in the order cell colours reference it. Note that
# the first two pairs are swapped relative to the theme XML's own order
# (lt1/dk1 rather than dk1/lt1) — a long-standing OOXML quirk.
_THEME_COLORS: Final[tuple[str, ...]] = (
    "FFFFFF",  # 0  background 1 (lt1)
    "000000",  # 1  text 1 (dk1)
    "E7E6E6",  # 2  background 2 (lt2)
    "44546A",  # 3  text 2 (dk2)
    "4472C4",  # 4  accent 1
    "ED7D31",  # 5  accent 2
    "A5A5A5",  # 6  accent 3
    "FFC000",  # 7  accent 4
    "5B9BD5",  # 8  accent 5
    "70AD47",  # 9  accent 6
    "0563C1",  # 10 hyperlink
    "954F72",  # 11 followed hyperlink
)

_BORDER_STYLE_BY_OPENPYXL: Final[dict[str, BorderStyle]] = {
    "thin": BorderStyle.THIN,
    "hair": BorderStyle.THIN,
    "medium": BorderStyle.MEDIUM,
    "thick": BorderStyle.THICK,
    "dashed": BorderStyle.DASHED,
    "mediumDashed": BorderStyle.DASHED,
    "dashDot": BorderStyle.DASHED,
    "mediumDashDot": BorderStyle.DASHED,
    "dashDotDot": BorderStyle.DASHED,
    "mediumDashDotDot": BorderStyle.DASHED,
    "slantDashDot": BorderStyle.DASHED,
    "dotted": BorderStyle.DOTTED,
    "double": BorderStyle.DOUBLE,
}

_OPENPYXL_BORDER_BY_STYLE: Final[dict[BorderStyle, str]] = {
    BorderStyle.THIN: "thin",
    BorderStyle.MEDIUM: "medium",
    BorderStyle.THICK: "thick",
    BorderStyle.DASHED: "dashed",
    BorderStyle.DOTTED: "dotted",
    BorderStyle.DOUBLE: "double",
}

_HORIZONTAL_BY_OPENPYXL: Final[dict[str, HorizontalAlignment]] = {
    "general": HorizontalAlignment.GENERAL,
    "left": HorizontalAlignment.LEFT,
    "center": HorizontalAlignment.CENTER,
    "centerContinuous": HorizontalAlignment.CENTER,
    "right": HorizontalAlignment.RIGHT,
    "justify": HorizontalAlignment.JUSTIFY,
    "distributed": HorizontalAlignment.JUSTIFY,
    "fill": HorizontalAlignment.LEFT,
}

# Excel calls the middle band "center"; the interchange model says "middle"
# to match the frontend's own vocabulary.
_VERTICAL_BY_OPENPYXL: Final[dict[str, VerticalAlignment]] = {
    "top": VerticalAlignment.TOP,
    "center": VerticalAlignment.MIDDLE,
    "bottom": VerticalAlignment.BOTTOM,
    "justify": VerticalAlignment.MIDDLE,
    "distributed": VerticalAlignment.MIDDLE,
}

_OPENPYXL_VERTICAL_BY_STYLE: Final[dict[VerticalAlignment, str]] = {
    VerticalAlignment.TOP: "top",
    VerticalAlignment.MIDDLE: "center",
    VerticalAlignment.BOTTOM: "bottom",
}


def _apply_tint(hex_rgb: str, tint: float) -> str:
    """Lighten or darken a colour the way Excel applies a theme tint.

    OOXML tints operate on luminance in HSL: negative darkens toward black,
    positive lightens toward white.
    """

    if not tint:
        return hex_rgb
    red, green, blue = (int(hex_rgb[i : i + 2], 16) / 255 for i in (0, 2, 4))
    hue, luminance, saturation = colorsys.rgb_to_hls(red, green, blue)
    if tint < 0:
        luminance = luminance * (1.0 + tint)
    else:
        luminance = luminance * (1.0 - tint) + tint
    red, green, blue = colorsys.hls_to_rgb(
        hue, min(max(luminance, 0.0), 1.0), saturation
    )
    return "".join(f"{round(channel * 255):02X}" for channel in (red, green, blue))


def resolve_color(color: Color | None) -> str | None:
    """Resolve any openpyxl colour to `#RRGGBB`, or None when unset."""

    if color is None:
        return None

    color_type = getattr(color, "type", None)
    tint = float(getattr(color, "tint", 0.0) or 0.0)

    if color_type == "rgb":
        raw = getattr(color, "rgb", None)
        if not isinstance(raw, str) or len(raw) not in (6, 8):
            return None
        # ARGB: drop the alpha channel, which Excel ignores for cells anyway.
        hex_rgb = raw[-6:].upper()
        if len(raw) == 8 and raw[:2].upper() == "00":
            # A fully transparent colour is "unset" in practice.
            return None
        return f"#{_apply_tint(hex_rgb, tint)}"

    if color_type == "theme":
        index = int(getattr(color, "theme", 0) or 0)
        if 0 <= index < len(_THEME_COLORS):
            return f"#{_apply_tint(_THEME_COLORS[index], tint)}"
        return None

    if color_type == "indexed":
        index = int(getattr(color, "indexed", 0) or 0)
        if 0 <= index < len(COLOR_INDEX):
            raw = COLOR_INDEX[index]
            if isinstance(raw, str) and len(raw) == 8:
                return f"#{_apply_tint(raw[-6:].upper(), tint)}"
        return None

    return None


def _border_edge(side: Side | None) -> BorderEdge | None:
    if side is None or not side.style:
        return None
    style = _BORDER_STYLE_BY_OPENPYXL.get(str(side.style), BorderStyle.THIN)
    return BorderEdge(style=style, color=resolve_color(side.color))


def read_cell_style(cell: Any, number_format: str | None) -> CellStyle:
    """Build an interchange style from an openpyxl cell."""

    font = cell.font
    fill = cell.fill
    alignment = cell.alignment
    border = cell.border

    background: str | None = None
    # Only solid fills carry a usable single colour; gradients are dropped.
    if fill is not None and getattr(fill, "patternType", None) == "solid":
        background = resolve_color(getattr(fill, "fgColor", None))

    horizontal = None
    vertical = None
    wrap = False
    if alignment is not None:
        if alignment.horizontal:
            horizontal = _HORIZONTAL_BY_OPENPYXL.get(str(alignment.horizontal))
        if alignment.vertical:
            vertical = _VERTICAL_BY_OPENPYXL.get(str(alignment.vertical))
        wrap = bool(alignment.wrap_text)

    return CellStyle(
        font_family=(font.name if font is not None else None) or None,
        font_size=float(font.size) if font is not None and font.size else None,
        bold=bool(font.bold) if font is not None else False,
        italic=bool(font.italic) if font is not None else False,
        underline=bool(font.underline) if font is not None else False,
        strikethrough=bool(font.strike) if font is not None else False,
        text_color=resolve_color(font.color) if font is not None else None,
        background_color=background,
        horizontal=(
            horizontal
            if horizontal is not None
            and horizontal is not HorizontalAlignment.GENERAL
            else None
        ),
        vertical=vertical,
        wrap_text=wrap,
        number_format=(
            number_format
            if number_format and number_format != "General"
            else None
        ),
        border_top=_border_edge(border.top) if border is not None else None,
        border_bottom=_border_edge(border.bottom) if border is not None else None,
        border_left=_border_edge(border.left) if border is not None else None,
        border_right=_border_edge(border.right) if border is not None else None,
    )


def _argb(color: str | None) -> str | None:
    """`#RRGGBB` to the opaque `AARRGGBB` string openpyxl expects."""

    if not color:
        return None
    value = color.lstrip("#").upper()
    return f"FF{value}" if len(value) == 6 else None


def _side(edge: BorderEdge | None) -> Side | None:
    if edge is None or edge.style is BorderStyle.NONE:
        return None
    argb = _argb(edge.color)
    return Side(
        style=_OPENPYXL_BORDER_BY_STYLE.get(edge.style, "thin"),
        color=Color(rgb=argb) if argb else None,
    )


class OpenpyxlStyleFactory:
    """Builds openpyxl style objects once per interchange style.

    Export writes the same handful of styles across thousands of cells;
    rebuilding `Font`/`Border` objects each time is pure waste, and openpyxl
    is happy to share immutable style objects between cells.
    """

    __slots__ = ("_cache",)

    def __init__(self) -> None:
        self._cache: dict[int, dict[str, Any]] = {}

    def build(self, style_id: int, style: CellStyle) -> dict[str, Any]:
        cached = self._cache.get(style_id)
        if cached is not None:
            return cached

        parts: dict[str, Any] = {}

        text_color = _argb(style.text_color)
        if (
            style.font_family
            or style.font_size
            or style.bold
            or style.italic
            or style.underline
            or style.strikethrough
            or text_color
        ):
            parts["font"] = Font(
                name=style.font_family or None,
                size=style.font_size or None,
                bold=style.bold or None,
                italic=style.italic or None,
                underline="single" if style.underline else None,
                strike=style.strikethrough or None,
                color=Color(rgb=text_color) if text_color else None,
            )

        background = _argb(style.background_color)
        if background:
            parts["fill"] = PatternFill(
                fill_type="solid",
                start_color=background,
                end_color=background,
            )

        if style.horizontal or style.vertical or style.wrap_text:
            parts["alignment"] = Alignment(
                horizontal=(
                    style.horizontal.value
                    if style.horizontal
                    and style.horizontal is not HorizontalAlignment.GENERAL
                    else None
                ),
                vertical=(
                    _OPENPYXL_VERTICAL_BY_STYLE.get(style.vertical)
                    if style.vertical
                    else None
                ),
                wrap_text=style.wrap_text or None,
            )

        sides = {
            "top": _side(style.border_top),
            "bottom": _side(style.border_bottom),
            "left": _side(style.border_left),
            "right": _side(style.border_right),
        }
        if any(side is not None for side in sides.values()):
            parts["border"] = Border(**sides)

        if style.number_format:
            parts["number_format"] = style.number_format

        self._cache[style_id] = parts
        return parts
