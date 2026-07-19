from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import fitz
from pydantic import BaseModel, Field


_SPACE_RE = re.compile(r"\s+")
_NUMBER_RE = re.compile(
    r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$"
)
_TABLE_EVIDENCE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("table_phrase", re.compile(r"\b(?:the\s+following\s+table|table\s+below)\b", re.I)),
    ("balance_sheet", re.compile(r"\bbalance\s+sheets?\b", re.I)),
    ("cash_flows", re.compile(r"\bstatement(?:s)?\s+of\s+cash\s+flows?\b", re.I)),
    ("consolidated_statement", re.compile(r"\bconsolidated\s+statement", re.I)),
    ("period_heading", re.compile(r"\b(?:year|months?)\s+ended\b", re.I)),
    ("scaled_units", re.compile(r"\bin\s+(?:thousands|millions|billions)\b", re.I)),
    ("comparison", re.compile(r"\b(?:feature|comparison)\b", re.I)),
    ("year_sequence", re.compile(r"\b20\d{2}\s+20\d{2}(?:\s+20\d{2})?\b")),
)


class DetectorThresholds(BaseModel):
    numeric_min_rows: int = Field(default=3, ge=2)
    numeric_min_columns: int = Field(default=2, ge=2)
    numeric_min_values: int = Field(default=6, ge=2)
    text_min_rows: int = Field(default=4, ge=2)
    text_min_columns: int = Field(default=3, ge=2)
    numeric_coverage: float = Field(default=0.70, ge=0, le=1)
    extraction_quality: float = Field(default=0.60, ge=0, le=1)
    row_tolerance: float = Field(default=3.0, gt=0)
    numeric_column_tolerance: float = Field(default=9.0, gt=0)
    text_column_tolerance: float = Field(default=5.0, gt=0)
    page_padding: int = Field(default=1, ge=0)
    max_pages_per_job: int = Field(default=12, ge=3)

    @classmethod
    def from_env(cls) -> "DetectorThresholds":
        return cls(
            numeric_coverage=float(
                os.getenv("DATA_ANALYSIS_TABLE_NUMERIC_COVERAGE", "0.70")
            ),
            extraction_quality=float(
                os.getenv("DATA_ANALYSIS_TABLE_QUALITY_THRESHOLD", "0.60")
            ),
            page_padding=int(os.getenv("DATA_ANALYSIS_DOCLING_PAGE_PADDING", "1")),
            max_pages_per_job=int(
                os.getenv("DATA_ANALYSIS_DOCLING_MAX_PAGES_PER_JOB", "12")
            ),
        )


class PageRange(BaseModel):
    page_start: int = Field(..., ge=1)
    page_end: int = Field(..., ge=1)
    flagged_pages: list[int] = Field(default_factory=list)


class PageTableAssessment(BaseModel):
    page: int = Field(..., ge=1)
    probable_table: bool
    numeric_value_count: int = 0
    aligned_numeric_value_count: int = 0
    aligned_numeric_row_count: int = 0
    repeated_numeric_columns: int = 0
    aligned_text_row_count: int = 0
    repeated_text_columns: int = 0
    text_evidence: list[str] = Field(default_factory=list)
    default_table_count: int = 0
    text_strategy_table_count: int = 0
    valid_text_strategy_candidate_count: int = 0
    numeric_coverage: float | None = None
    extraction_quality: float | None = None
    needs_docling: bool = False
    reasons: list[str] = Field(default_factory=list)


class TableCoverageReport(BaseModel):
    total_pages: int = Field(..., ge=1)
    flagged_pages: list[int] = Field(default_factory=list)
    page_ranges: list[PageRange] = Field(default_factory=list)
    pages: list[PageTableAssessment] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass(slots=True)
class _ColumnCluster:
    anchor: float
    members: list[tuple[int, _Word]]

    @property
    def row_count(self) -> int:
        return len({row_index for row_index, _ in self.members})


@dataclass(frozen=True, slots=True)
class _Alignment:
    row_count: int
    column_count: int
    words: tuple[_Word, ...]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\u00a0", " ")).strip()


def _is_numeric_token(value: str) -> bool:
    text = _clean(value).strip(".,;:")
    if not text or text in {"$", "₹", "€", "£", "%", "-", "—", "–"}:
        return False
    text = text.replace("−", "-").replace("₹", "").replace("$", "")
    text = text.replace("€", "").replace("£", "").strip()
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1].strip()}"
    return bool(_NUMBER_RE.fullmatch(text))


def _page_words(page: fitz.Page) -> list[_Word]:
    return [
        _Word(
            x0=float(word[0]),
            y0=float(word[1]),
            x1=float(word[2]),
            y1=float(word[3]),
            text=_clean(word[4]),
        )
        for word in page.get_text("words", sort=True)
        if _clean(word[4])
    ]


def _visual_rows(words: Sequence[_Word], tolerance: float) -> list[list[_Word]]:
    rows: list[dict[str, Any]] = []
    for word in sorted(words, key=lambda item: (item.center_y, item.x0)):
        closest: dict[str, Any] | None = None
        closest_distance = math.inf
        for row in reversed(rows):
            distance = abs(float(row["center"]) - word.center_y)
            if distance <= tolerance and distance < closest_distance:
                closest = row
                closest_distance = distance
            if float(row["center"]) < word.center_y - tolerance:
                break
        if closest is None:
            rows.append({"center": word.center_y, "words": [word]})
            continue
        closest["words"].append(word)
        count = len(closest["words"])
        closest["center"] = (
            (float(closest["center"]) * (count - 1)) + word.center_y
        ) / count
    return [
        sorted(row["words"], key=lambda item: item.x0)
        for row in sorted(rows, key=lambda item: float(item["center"]))
    ]


def _cluster_columns(
    rows: Sequence[Sequence[_Word]],
    *,
    anchor_getter: Any,
    tolerance: float,
    minimum_rows: int,
) -> list[_ColumnCluster]:
    clusters: list[_ColumnCluster] = []
    for row_index, row in enumerate(rows):
        used: set[int] = set()
        for word in row:
            anchor = float(anchor_getter(word))
            choices = [
                (abs(cluster.anchor - anchor), index)
                for index, cluster in enumerate(clusters)
                if index not in used and abs(cluster.anchor - anchor) <= tolerance
            ]
            if not choices:
                clusters.append(_ColumnCluster(anchor=anchor, members=[(row_index, word)]))
                used.add(len(clusters) - 1)
                continue
            _, cluster_index = min(choices)
            cluster = clusters[cluster_index]
            cluster.members.append((row_index, word))
            cluster.anchor = sum(
                float(anchor_getter(member)) for _, member in cluster.members
            ) / len(cluster.members)
            used.add(cluster_index)
    return [cluster for cluster in clusters if cluster.row_count >= minimum_rows]


def _numeric_alignment(
    rows: Sequence[Sequence[_Word]], thresholds: DetectorThresholds
) -> _Alignment:
    numeric_rows = [
        [word for word in row if _is_numeric_token(word.text)] for row in rows
    ]
    clusters = _cluster_columns(
        numeric_rows,
        anchor_getter=lambda word: word.x1,
        tolerance=thresholds.numeric_column_tolerance,
        minimum_rows=thresholds.numeric_min_rows,
    )
    memberships: dict[int, set[int]] = {}
    for cluster_index, cluster in enumerate(clusters):
        for row_index, _ in cluster.members:
            memberships.setdefault(row_index, set()).add(cluster_index)
    aligned_rows = {
        row_index
        for row_index, column_indexes in memberships.items()
        if len(column_indexes) >= thresholds.numeric_min_columns
    }
    aligned_words = tuple(
        word
        for cluster in clusters
        for row_index, word in cluster.members
        if row_index in aligned_rows
    )
    active_columns = {
        cluster_index
        for cluster_index, cluster in enumerate(clusters)
        if any(row_index in aligned_rows for row_index, _ in cluster.members)
    }
    return _Alignment(
        row_count=len(aligned_rows),
        column_count=len(active_columns),
        words=aligned_words,
    )


def _text_alignment(
    rows: Sequence[Sequence[_Word]], thresholds: DetectorThresholds
) -> _Alignment:
    # Turn each visual line into whitespace-separated cells before looking for
    # columns. Clustering individual word positions makes ordinary paragraphs
    # look tabular; paragraphs become one cell with this approach.
    text_rows: list[list[_Word]] = []
    for row in rows:
        cells: list[list[_Word]] = []
        for word in row:
            if not cells:
                cells.append([word])
                continue
            previous = cells[-1][-1]
            typical_height = max(1.0, min(word.y1 - word.y0, previous.y1 - previous.y0))
            if word.x0 - previous.x1 <= max(12.0, typical_height * 1.6):
                cells[-1].append(word)
            else:
                cells.append([word])

        cell_words = [
            _Word(
                x0=cell[0].x0,
                y0=min(word.y0 for word in cell),
                x1=cell[-1].x1,
                y1=max(word.y1 for word in cell),
                text=" ".join(word.text for word in cell),
            )
            for cell in cells
        ]
        textual_cells = [
            cell
            for cell in cell_words
            if not _is_numeric_token(cell.text)
            and len(re.sub(r"\W+", "", cell.text, flags=re.UNICODE)) >= 2
        ]
        text_rows.append(textual_cells if 3 <= len(textual_cells) <= 12 else [])
    clusters = _cluster_columns(
        text_rows,
        anchor_getter=lambda word: word.x0,
        tolerance=thresholds.text_column_tolerance,
        minimum_rows=thresholds.text_min_rows,
    )
    memberships: dict[int, set[int]] = {}
    for cluster_index, cluster in enumerate(clusters):
        for row_index, _ in cluster.members:
            memberships.setdefault(row_index, set()).add(cluster_index)
    aligned_rows = {
        row_index
        for row_index, column_indexes in memberships.items()
        if len(column_indexes) >= thresholds.text_min_columns
    }
    aligned_words = tuple(
        word
        for cluster in clusters
        for row_index, word in cluster.members
        if row_index in aligned_rows
    )
    active_columns = {
        cluster_index
        for cluster_index, cluster in enumerate(clusters)
        if any(row_index in aligned_rows for row_index, _ in cluster.members)
    }
    return _Alignment(
        row_count=len(aligned_rows),
        column_count=len(active_columns),
        words=aligned_words,
    )


def _inside_any_table(word: _Word, bboxes: Sequence[fitz.Rect]) -> bool:
    center = fitz.Point((word.x0 + word.x1) / 2, word.center_y)
    return any(bbox.contains(center) for bbox in bboxes)


def _matrix(table: Any) -> list[list[str]]:
    try:
        extracted = table.extract()
    except Exception:
        return []
    width = max((len(row) for row in extracted), default=0)
    return [
        [_clean(row[index]) if index < len(row) else "" for index in range(width)]
        for row in extracted
    ]


def _quality_score(table: Any) -> float:
    matrix = _matrix(table)
    if not matrix:
        return 0.0
    row_count = len(matrix)
    column_count = max((len(row) for row in matrix), default=0)
    structure = 1.0 if row_count >= 2 and column_count >= 2 else 0.0
    width = 1.0 if column_count <= 20 else 0.5 if column_count <= 24 else 0.0
    populated_rows = sum(sum(bool(value) for value in row) >= 2 for row in matrix)
    density = min(1.0, (populated_rows / max(1, row_count)) / 0.70)

    header = [value for value in matrix[0] if value]
    fragmented = sum(len(re.sub(r"\W+", "", value)) <= 1 for value in header)
    header_quality = 1.0 - (fragmented / max(1, len(header)))
    canonical_header = tuple(value.casefold() for value in matrix[0])
    repeated_headers = sum(
        tuple(value.casefold() for value in row) == canonical_header
        for row in matrix[1:]
    )
    repetition_quality = 0.0 if repeated_headers > max(1, row_count // 5) else 1.0
    return round(
        (structure + width + density + header_quality + repetition_quality) / 5,
        4,
    )


def _valid_text_candidate(table: Any) -> bool:
    matrix = _matrix(table)
    if len(matrix) < 4:
        return False
    column_count = max((len(row) for row in matrix), default=0)
    if not 3 <= column_count <= 24:
        return False
    useful_rows = sum(sum(bool(value) for value in row) >= 2 for row in matrix)
    return useful_rows / len(matrix) >= 0.40


def _rect_overlap_ratio(candidate: fitz.Rect, extracted: fitz.Rect) -> float:
    intersection = candidate & extracted
    if intersection.is_empty or candidate.get_area() <= 0:
        return 0.0
    return intersection.get_area() / candidate.get_area()


def _text_evidence(page_text: str) -> list[str]:
    return [name for name, pattern in _TABLE_EVIDENCE if pattern.search(page_text)]


def _find_tables(page: fitz.Page, **kwargs: Any) -> list[Any]:
    try:
        return list(page.find_tables(**kwargs).tables)
    except Exception:
        return []


def assess_page(
    page: fitz.Page,
    *,
    page_number: int,
    thresholds: DetectorThresholds,
) -> PageTableAssessment:
    words = _page_words(page)
    rows = _visual_rows(words, thresholds.row_tolerance)
    numeric = _numeric_alignment(rows, thresholds)
    textual = _text_alignment(rows, thresholds)
    numeric_value_count = sum(_is_numeric_token(word.text) for word in words)
    evidence = _text_evidence(page.get_text("text", sort=True))

    probable_numeric = (
        numeric.row_count >= thresholds.numeric_min_rows
        and numeric.column_count >= thresholds.numeric_min_columns
        and len(numeric.words) >= thresholds.numeric_min_values
    )
    probable_text = (
        textual.row_count >= thresholds.text_min_rows
        and textual.column_count >= thresholds.text_min_columns
    )
    probable_table = probable_numeric or probable_text

    default_tables = _find_tables(page)
    text_tables = _find_tables(page, strategy="text")
    default_bboxes = [fitz.Rect(table.bbox) for table in default_tables]
    valid_text_tables = [table for table in text_tables if _valid_text_candidate(table)]

    numeric_coverage: float | None = None
    if numeric.words:
        covered = sum(_inside_any_table(word, default_bboxes) for word in numeric.words)
        numeric_coverage = round(covered / len(numeric.words), 4)

    qualities = [_quality_score(table) for table in default_tables]
    extraction_quality = round(min(qualities), 4) if qualities else None
    uncovered_text_candidate = any(
        not any(
            _rect_overlap_ratio(fitz.Rect(candidate.bbox), bbox) >= 0.70
            for bbox in default_bboxes
        )
        for candidate in valid_text_tables
    )

    reasons: list[str] = []
    if probable_numeric:
        reasons.append("aligned_numeric_rows_detected")
    if probable_text:
        reasons.append("aligned_text_rows_detected")
    if evidence and probable_table:
        reasons.append("table_text_evidence_detected")

    needs_docling = False
    if probable_table and not default_tables:
        needs_docling = True
        reasons.append("no_default_table_detected")
    if (
        probable_numeric
        and numeric_coverage is not None
        and numeric_coverage < thresholds.numeric_coverage
    ):
        needs_docling = True
        reasons.append("low_numeric_coverage")
    if (
        probable_table
        and extraction_quality is not None
        and extraction_quality < thresholds.extraction_quality
    ):
        needs_docling = True
        reasons.append("low_extraction_quality")
    if probable_text and valid_text_tables and uncovered_text_candidate:
        needs_docling = True
        reasons.append("valid_text_strategy_candidate_not_covered")

    return PageTableAssessment(
        page=page_number,
        probable_table=probable_table,
        numeric_value_count=numeric_value_count,
        aligned_numeric_value_count=len(numeric.words),
        aligned_numeric_row_count=numeric.row_count,
        repeated_numeric_columns=numeric.column_count,
        aligned_text_row_count=textual.row_count,
        repeated_text_columns=textual.column_count,
        text_evidence=evidence,
        default_table_count=len(default_tables),
        text_strategy_table_count=len(text_tables),
        valid_text_strategy_candidate_count=len(valid_text_tables),
        numeric_coverage=numeric_coverage,
        extraction_quality=extraction_quality,
        needs_docling=needs_docling,
        reasons=reasons,
    )


def group_flagged_pages(
    flagged_pages: Iterable[int],
    *,
    total_pages: int,
    padding: int = 1,
    max_pages_per_job: int = 12,
) -> list[PageRange]:
    pages = sorted({page for page in flagged_pages if 1 <= page <= total_pages})
    if not pages:
        return []

    consecutive_groups: list[list[int]] = [[pages[0]]]
    for page in pages[1:]:
        if page == consecutive_groups[-1][-1] + 1:
            consecutive_groups[-1].append(page)
        else:
            consecutive_groups.append([page])

    # Bound each Docling call even when a long financial section is entirely
    # table-like. Padding is retained around every chunk for titles and
    # multi-page continuations.
    flagged_capacity = max(1, max_pages_per_job - (2 * padding))
    ranges: list[PageRange] = []
    for group in consecutive_groups:
        for offset in range(0, len(group), flagged_capacity):
            chunk = group[offset : offset + flagged_capacity]
            ranges.append(
                PageRange(
                    page_start=max(1, chunk[0] - padding),
                    page_end=min(total_pages, chunk[-1] + padding),
                    flagged_pages=chunk,
                )
            )
    return ranges


def analyze_pdf_table_coverage(
    pdf_path: str | Path,
    *,
    thresholds: DetectorThresholds | None = None,
    page_numbers: Iterable[int] | None = None,
) -> TableCoverageReport:
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")
    selected_thresholds = thresholds or DetectorThresholds.from_env()

    with fitz.open(path) as document:
        total_pages = len(document)
        selected_pages = (
            sorted({page for page in page_numbers if 1 <= page <= total_pages})
            if page_numbers is not None
            else list(range(1, total_pages + 1))
        )
        assessments = [
            assess_page(
                document[page_number - 1],
                page_number=page_number,
                thresholds=selected_thresholds,
            )
            for page_number in selected_pages
        ]

    flagged_pages = [page.page for page in assessments if page.needs_docling]
    return TableCoverageReport(
        total_pages=total_pages,
        flagged_pages=flagged_pages,
        page_ranges=group_flagged_pages(
            flagged_pages,
            total_pages=total_pages,
            padding=selected_thresholds.page_padding,
            max_pages_per_job=selected_thresholds.max_pages_per_job,
        ),
        pages=assessments,
    )
