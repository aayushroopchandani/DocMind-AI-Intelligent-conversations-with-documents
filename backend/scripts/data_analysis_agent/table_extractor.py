from __future__ import annotations

import bisect
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Sequence

import fitz

from db.models.structured_table import (
    StructuredTable,
    TableColumn,
    TableSourceFragment,
)


_SPACE_RE = re.compile(r"\s+")
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?$")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")
_PERIOD_RE = re.compile(
    r"^(?:(?:19|20)\d{2}|q[1-4](?:\s+(?:19|20)\d{2})?|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)$",
    re.IGNORECASE,
)
_UNIT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bINR\s+(?:crore|lakh|million|billion)\b", re.I), "INR {scale}"),
    (re.compile(r"\bUSD\s+(?:million|billion|thousand)\b", re.I), "USD {scale}"),
    (re.compile(r"\b(?:in\s+)?USD\b|\bUS\s*dollars?\b", re.I), "USD"),
    (re.compile(r"\b(?:in\s+)?INR\b|\bIndian\s+rupees?\b", re.I), "INR"),
    (re.compile(r"\bEUR\b", re.I), "EUR"),
    (re.compile(r"\bGBP\b", re.I), "GBP"),
    (re.compile(r"%|\bpercent(?:age)?\b", re.I), "%"),
)


@dataclass(slots=True)
class _Fragment:
    page: int
    page_width: float
    page_height: float
    bbox: tuple[float, float, float, float]
    header: list[str]
    rows: list[list[str]]
    sources: list[TableSourceFragment] = field(default_factory=list)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\u00a0", " ")).strip()


def _canonical(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean(value).casefold()).strip()


def _rectangular(matrix: Sequence[Sequence[Any]]) -> list[list[str]]:
    width = max((len(row) for row in matrix), default=0)
    return [
        [_clean(row[index]) if index < len(row) else "" for index in range(width)]
        for row in matrix
    ]


def _cell_lines(page: fitz.Page, bbox: Sequence[float] | None) -> list[tuple[float, str]]:
    """Return visual text lines with their vertical centers for one table cell."""
    if not bbox:
        return []

    words = page.get_text("words", clip=fitz.Rect(bbox), sort=True)
    lines: list[dict[str, Any]] = []
    for word in words:
        y0, y1, text = float(word[1]), float(word[3]), _clean(word[4])
        if not text:
            continue
        center = (y0 + y1) / 2
        existing = next((line for line in lines if abs(line["center"] - center) <= 2.0), None)
        if existing is None:
            lines.append({"center": center, "parts": [(float(word[0]), text)]})
        else:
            existing["parts"].append((float(word[0]), text))

    output: list[tuple[float, str]] = []
    for line in sorted(lines, key=lambda item: item["center"]):
        text = " ".join(part for _, part in sorted(line["parts"]))
        output.append((float(line["center"]), _clean(text)))
    return output


def _numeric_text(value: str) -> bool:
    text = _clean(value)
    if not text or text in {"-", "—", "–"}:
        return False
    text = re.sub(r"^(?:USD|INR|EUR|GBP)\s*", "", text, flags=re.I)
    text = text.replace(",", "").replace("₹", "").replace("$", "")
    text = text.rstrip("%").strip()
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1].strip()}"
    return bool(_NUMBER_RE.fullmatch(text))


def _anchor_value(value: str) -> bool:
    return _numeric_text(value) or bool(_DATE_RE.fullmatch(_clean(value)))


def _choose_anchor_lines(cell_lines: list[list[tuple[float, str]]]) -> list[float]:
    candidates: list[tuple[float, int, list[float]]] = []
    for index, lines in enumerate(cell_lines):
        if not lines:
            continue
        numeric_ratio = sum(_anchor_value(text) for _, text in lines) / len(lines)
        if numeric_ratio < 0.5:
            continue
        # Prefer a numeric measure column. Its baselines correspond to actual
        # records, while a label cell may contain extra wrapped display lines.
        score = numeric_ratio * 100 + len(lines) - (index * 0.001)
        candidates.append((score, len(lines), [center for center, _ in lines]))

    if not candidates:
        # Purely textual comparison/catalog tables usually use one PyMuPDF row
        # per logical record; multiple visual lines are wrapping, not records.
        return [0.0] if any(cell_lines) else []
    candidates.sort(reverse=True, key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _visual_rows(page: fitz.Page, table: Any, start_row: int = 1) -> list[list[str]]:
    """Expand PyMuPDF physical rows into visually aligned logical rows."""
    output: list[list[str]] = []
    for physical_row in list(table.rows)[start_row:]:
        cell_lines = [_cell_lines(page, bbox) for bbox in physical_row.cells]
        anchors = _choose_anchor_lines(cell_lines)
        if not anchors:
            continue

        midpoints = [
            (anchors[index] + anchors[index + 1]) / 2
            for index in range(len(anchors) - 1)
        ]
        buckets: list[list[list[str]]] = [
            [[] for _ in cell_lines] for _ in anchors
        ]
        for column_index, lines in enumerate(cell_lines):
            for center, text in lines:
                row_index = bisect.bisect_left(midpoints, center)
                row_index = min(row_index, len(anchors) - 1)
                buckets[row_index][column_index].append(text)

        for bucket in buckets:
            row = [_clean(" ".join(parts)) for parts in bucket]
            # A label/footer with no corresponding values is not a table row.
            if sum(bool(value) for value in row) >= 2:
                output.append(row)
    return output


def _extract_page_fragments(page: fitz.Page, page_number: int) -> list[_Fragment]:
    fragments: list[_Fragment] = []
    for table in page.find_tables().tables:
        matrix = _rectangular(table.extract())
        if len(matrix) < 2 or len(matrix[0]) < 2:
            continue
        bbox = tuple(float(value) for value in table.bbox)
        fragments.append(
            _Fragment(
                page=page_number,
                page_width=float(page.rect.width),
                page_height=float(page.rect.height),
                bbox=bbox,  # type: ignore[arg-type]
                header=matrix[0],
                rows=_visual_rows(page, table),
                sources=[
                    TableSourceFragment(page=page_number, bounding_box=list(bbox))
                ],
            )
        )
    return sorted(fragments, key=lambda fragment: (fragment.bbox[1], fragment.bbox[0]))


def _is_period_label(value: str) -> bool:
    without_unit = re.sub(
        r"\b(?:in\s+)?(?:USD|INR|EUR|GBP)\b|%", "", _clean(value), flags=re.I
    )
    return bool(_PERIOD_RE.fullmatch(_clean(without_unit)))


def _join_horizontal_continuations(fragments: list[_Fragment]) -> list[_Fragment]:
    """Join visually wrapped wide tables, such as 2019–20 above 2021–23."""
    joined: list[_Fragment] = []
    index = 0
    while index < len(fragments):
        current = fragments[index]
        if index + 1 >= len(fragments):
            joined.append(current)
            break

        following = fragments[index + 1]
        current_periods = current.header[1:]
        same_row_count = bool(current.rows) and len(current.rows) == len(following.rows)
        left_is_dimension = sum(
            bool(row and row[0] and not _numeric_text(row[0])) for row in current.rows
        ) >= max(1, len(current.rows) // 2)
        following_is_numeric = bool(following.rows) and all(
            all(not value or _numeric_text(value) for value in row)
            for row in following.rows
        )
        is_continuation = (
            current.page == following.page
            and current.bbox[1] < following.bbox[1]
            and same_row_count
            and left_is_dimension
            and following_is_numeric
            and bool(current_periods)
            and all(_is_period_label(value) for value in current_periods)
            and all(_is_period_label(value) for value in following.header)
        )

        if not is_continuation:
            joined.append(current)
            index += 1
            continue

        current.header.extend(following.header)
        current.rows = [
            left_row + right_row
            for left_row, right_row in zip(current.rows, following.rows, strict=True)
        ]
        current.sources.extend(following.sources)
        current.bbox = (
            min(current.bbox[0], following.bbox[0]),
            min(current.bbox[1], following.bbox[1]),
            max(current.bbox[2], following.bbox[2]),
            max(current.bbox[3], following.bbox[3]),
        )
        joined.append(current)
        index += 2
    return joined


def _header_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    scores = [
        SequenceMatcher(None, _canonical(a), _canonical(b)).ratio()
        for a, b in zip(left, right, strict=True)
    ]
    return sum(scores) / len(scores)


def _merge_multi_page_fragments(fragments: list[_Fragment]) -> list[_Fragment]:
    merged: list[_Fragment] = []
    for fragment in fragments:
        if not merged:
            merged.append(fragment)
            continue

        previous = merged[-1]
        touches_page_bottom = previous.bbox[3] >= previous.page_height * 0.86
        starts_near_page_top = fragment.bbox[1] <= fragment.page_height * 0.16
        same_width = abs(
            (previous.bbox[2] - previous.bbox[0])
            - (fragment.bbox[2] - fragment.bbox[0])
        ) <= max(previous.page_width, fragment.page_width) * 0.12
        continues = (
            fragment.page == previous.sources[-1].page + 1
            and len(fragment.header) == len(previous.header)
            and touches_page_bottom
            and starts_near_page_top
            and same_width
            and _header_similarity(previous.header, fragment.header) >= 0.78
        )
        if not continues:
            merged.append(fragment)
            continue

        previous.rows.extend(fragment.rows)
        previous.sources.extend(fragment.sources)
    return merged


def _nearby_title(page: fitz.Page, bbox: Sequence[float]) -> str:
    candidates: list[tuple[float, str]] = []
    left, top, right, _ = bbox
    for block in page.get_text("blocks", sort=True):
        x0, y0, x1, y1, text = map(lambda value: value, block[:5])
        cleaned = _clean(text)
        horizontal_overlap = min(right, float(x1)) - max(left, float(x0))
        distance = top - float(y1)
        if cleaned and horizontal_overlap > 0 and 0 <= distance <= 80:
            candidates.append((distance, cleaned))
    if not candidates:
        return ""
    return min(candidates, key=lambda item: item[0])[1][:180]


def _title_for_fragment(fragment: _Fragment, page: fitz.Page) -> str:
    first_header = _clean(fragment.header[0]) if fragment.header else ""
    nearby = _nearby_title(page, fragment.bbox)
    generic = _canonical(first_header) in {
        "category", "date", "description", "feature", "item", "name", "metric"
    }
    if nearby and (generic or not first_header):
        return nearby
    return first_header or nearby or f"Table on page {fragment.page}"


def _detect_unit(value: str) -> str | None:
    text = _clean(value)
    if "₹" in text:
        return "INR"
    if "$" in text:
        return "USD"
    for pattern, normalized in _UNIT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if "{scale}" in normalized:
            scale = match.group(0).split()[-1].lower()
            return normalized.format(scale=scale)
        return normalized
    return None


def _column_label(value: str, index: int) -> str:
    text = _clean(value)
    text = re.sub(r"\bIn\s+(?:USD|INR|EUR|GBP)\b", "", text, flags=re.I)
    return _clean(text) or f"Column {index + 1}"


def _key_for_label(label: str, index: int, *, title: str) -> str:
    normalized = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    key = re.sub(r"[^a-z0-9]+", "_", normalized.casefold()).strip("_")
    if index == 0 and _canonical(label) == _canonical(title) and len(label) > 36:
        key = "item"
    if not key:
        key = f"column_{index + 1}"
    if key[0].isdigit():
        key = f"column_{key}"
    return key[:64]


def _unique_keys(labels: Sequence[str], *, title: str) -> list[str]:
    counts: dict[str, int] = {}
    keys: list[str] = []
    for index, label in enumerate(labels):
        base = _key_for_label(label, index, title=title)
        counts[base] = counts.get(base, 0) + 1
        keys.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return keys


def _parse_scalar(value: str) -> Any:
    text = _clean(value)
    if not text or text in {"-", "—", "–", "N/A", "n/a"}:
        return None
    lowered = text.casefold()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False

    numeric = re.sub(r"^(?:USD|INR|EUR|GBP)\s*", "", text, flags=re.I)
    numeric = numeric.replace(",", "").replace("₹", "").replace("$", "")
    numeric = numeric.rstrip("%").strip()
    if numeric.startswith("(") and numeric.endswith(")"):
        numeric = f"-{numeric[1:-1].strip()}"
    if _NUMBER_RE.fullmatch(numeric):
        number = float(numeric)
        return int(number) if number.is_integer() else number
    return text


def _infer_type(values: Iterable[Any]) -> str:
    present = [value for value in values if value is not None]
    if not present:
        return "string"
    if all(isinstance(value, bool) for value in present):
        return "boolean"
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in present):
        return "number"
    if all(isinstance(value, str) and _DATE_RE.fullmatch(value) for value in present):
        return "date"
    return "string"


def _node_for_page(nodes: Sequence[dict[str, Any]] | None, page: int) -> str | None:
    if not nodes:
        return None
    matching = [
        node for node in nodes
        if int(node.get("page_start", 0)) <= page <= int(node.get("page_end", 0))
    ]
    if not matching:
        return None
    matching.sort(key=lambda node: int(node.get("level", 0)), reverse=True)
    return str(matching[0].get("node_id")) if matching[0].get("node_id") else None


def _table_id(document_id: str, fragment: _Fragment, title: str) -> str:
    positions = ";".join(
        f"{source.page}:{','.join(f'{value:.2f}' for value in source.bounding_box)}"
        for source in fragment.sources
    )
    digest = hashlib.sha256(f"{document_id}|{title}|{positions}".encode()).hexdigest()[:20]
    return f"table_{digest}"


def _fallback_node_id(*, document_id: str, title: str, page: int) -> str:
    readable = re.sub(r"[^a-z0-9]+", "_", _canonical(title)).strip("_")[:40]
    digest = hashlib.sha256(f"{document_id}|{title}|{page}".encode()).hexdigest()[:8]
    return f"node_{readable or 'table'}_{digest}"


def _years_in_table(labels: Sequence[str], rows: Sequence[dict[str, Any]]) -> list[int]:
    years = {int(year) for label in labels for year in _YEAR_RE.findall(label)}
    for row in rows:
        for value in row.values():
            if isinstance(value, str) and _DATE_RE.fullmatch(value):
                years.update(int(year) for year in _YEAR_RE.findall(value))
    return sorted(years)


def build_deterministic_summary(table: StructuredTable) -> str:
    labels = [column.label for column in table.columns]
    dimensions = [column.label for column in table.columns if column.type != "number"]
    units = list(dict.fromkeys(column.unit for column in table.columns if column.unit))
    years = _years_in_table(labels, table.rows)

    row_sentence = f"Contains {len(table.rows)} rows"
    if years:
        date_range = str(years[0]) if len(years) == 1 else f"{years[0]}–{years[-1]}"
        row_sentence += f" covering {date_range}"
    row_sentence += "."

    return "\n".join(
        (
            f"Table: {table.title}",
            f"Columns: {', '.join(labels)}",
            row_sentence,
            f"Dimensions: {', '.join(dimensions) if dimensions else 'none'}",
            f"Units: {', '.join(units) if units else 'none'}",
        )
    )


def _normalize_fragment(
    fragment: _Fragment,
    *,
    document_id: str,
    user_id: str,
    chat_id: str | None,
    nodes: Sequence[dict[str, Any]] | None,
    page: fitz.Page,
) -> StructuredTable | None:
    width = len(fragment.header)
    if width < 2:
        return None
    title = _title_for_fragment(fragment, page)
    labels = [_column_label(value, index) for index, value in enumerate(fragment.header)]
    keys = _unique_keys(labels, title=title)

    parsed_rows: list[dict[str, Any]] = []
    for raw_row in fragment.rows:
        values = list(raw_row[:width]) + [""] * max(0, width - len(raw_row))
        parsed = {key: _parse_scalar(value) for key, value in zip(keys, values, strict=True)}
        if sum(value is not None for value in parsed.values()) >= 2:
            parsed_rows.append(parsed)
    if not parsed_rows:
        return None

    global_unit = _detect_unit(" ".join(fragment.header + [title]))
    columns: list[TableColumn] = []
    for index, (key, label, raw_header) in enumerate(
        zip(keys, labels, fragment.header, strict=True)
    ):
        column_type = _infer_type(row.get(key) for row in parsed_rows)
        observed_units = [
            _detect_unit(str(row[index]))
            for row in fragment.rows
            if index < len(row) and row[index]
        ]
        unit = _detect_unit(raw_header) or next((value for value in observed_units if value), None)
        if unit is None and column_type == "number":
            unit = global_unit
        columns.append(TableColumn(key=key, label=label, type=column_type, unit=unit))

    table = StructuredTable(
        table_id=_table_id(document_id, fragment, title),
        document_id=document_id,
        user_id=user_id,
        chat_id=chat_id,
        node_id=_node_for_page(nodes, fragment.page)
        or _fallback_node_id(
            document_id=document_id,
            title=title,
            page=fragment.page,
        ),
        page_start=fragment.sources[0].page,
        page_end=fragment.sources[-1].page,
        title=title,
        columns=columns,
        rows=parsed_rows,
        source_fragments=fragment.sources,
    )
    table.deterministic_summary = build_deterministic_summary(table)
    return table


def extract_tables_from_pdf(
    pdf_path: str | Path,
    *,
    document_id: str,
    user_id: str,
    chat_id: str | None = None,
    nodes: Sequence[dict[str, Any]] | None = None,
) -> list[StructuredTable]:
    """
    Detect, normalize, and merge PDF tables with PyMuPDF.

    Tables remain in the ordinary page text used by chunk ingestion; this
    function creates an additional structured representation and never removes
    or rewrites page content.
    """
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")

    with fitz.open(path) as document:
        fragments: list[_Fragment] = []
        pages_by_number: dict[int, fitz.Page] = {}
        for page_index, page in enumerate(document):
            page_number = page_index + 1
            pages_by_number[page_number] = page
            page_fragments = _extract_page_fragments(page, page_number)
            fragments.extend(_join_horizontal_continuations(page_fragments))

        merged = _merge_multi_page_fragments(fragments)
        tables = [
            _normalize_fragment(
                fragment,
                document_id=document_id,
                user_id=user_id,
                chat_id=chat_id,
                nodes=nodes,
                page=pages_by_number[fragment.page],
            )
            for fragment in merged
        ]
    return [table for table in tables if table is not None]
