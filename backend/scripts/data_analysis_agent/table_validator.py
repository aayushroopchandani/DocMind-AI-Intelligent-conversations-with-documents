from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Sequence

from pydantic import BaseModel, Field

from db.models.structured_table import StructuredTable


ValidationStatus = Literal["accepted", "quarantined", "rejected"]

_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_GENERIC_HEADER_RE = re.compile(
    r"^(?:column|col|field|unnamed|value)(?:[\s_-]*\d+)?$", re.IGNORECASE
)
_NUMBER_RE = re.compile(
    r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:%|[kmb])?$",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"^(?:"
    r"\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?"
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"
    r"|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+\d{1,2},?\s+\d{4}"
    r")$",
    re.IGNORECASE,
)
_DOT_LEADER_RE = re.compile(r"(?:\.\s*){4,}")
_UNIT_ONLY_RE = re.compile(
    r"^\(?\s*(?:(?:usd|eur|gbp|inr|dollars?|rupees?)\s+)?"
    r"(?:in\s+)?(?:usd|eur|gbp|inr|dollars?|rupees?|thousands?|"
    r"millions?|billions?|percent|percentage)(?:\s+of\s+dollars?)?\s*\)?$",
    re.IGNORECASE,
)
_NAVIGATION_TITLE_RE = re.compile(
    r"^(?:table\s+of\s+contents|contents|index\s+to\s+exhibits|"
    r"exhibit\s+index|signatures?)$",
    re.IGNORECASE,
)
_CONTEXT_CELL_RE = re.compile(
    r"^(?:were|was|are|is)?\s*(?:as\s+follows|shown\s+below|"
    r"summarized\s+below|set\s+forth\s+below)\s*:?$",
    re.IGNORECASE,
)


class TableValidationThresholds(BaseModel):
    """Source-aware thresholds for normalized structured tables."""

    pymupdf_accept_score: float = Field(default=0.70, ge=0, le=1)
    docling_accept_score: float = Field(default=0.55, ge=0, le=1)
    max_title_length: int = Field(default=140, ge=40)
    sparse_cell_ratio: float = Field(default=0.30, ge=0, le=1)

    @classmethod
    def from_env(cls) -> "TableValidationThresholds":
        return cls(
            pymupdf_accept_score=float(
                os.getenv("DATA_ANALYSIS_PYMUPDF_TABLE_QUALITY_THRESHOLD", "0.70")
            ),
            docling_accept_score=float(
                os.getenv("DATA_ANALYSIS_DOCLING_TABLE_QUALITY_THRESHOLD", "0.55")
            ),
            max_title_length=int(
                os.getenv("DATA_ANALYSIS_TABLE_MAX_TITLE_LENGTH", "140")
            ),
        )


@dataclass(frozen=True, slots=True)
class TableQualityAssessment:
    table_id: str
    extraction_method: str
    status: ValidationStatus
    score: float
    reasons: tuple[str, ...]
    populated_cell_ratio: float
    meaningful_column_ratio: float
    generic_header_ratio: float
    numeric_or_date_value_count: int


@dataclass(slots=True)
class TableValidationResult:
    accepted: list[StructuredTable] = field(default_factory=list)
    quarantined: list[StructuredTable] = field(default_factory=list)
    rejected: list[StructuredTable] = field(default_factory=list)
    assessments: list[TableQualityAssessment] = field(default_factory=list)

    @property
    def quarantined_pages(self) -> list[int]:
        return sorted(
            {
                page
                for table in self.quarantined
                for page in range(table.page_start, table.page_end + 1)
            }
        )


@dataclass(frozen=True, slots=True)
class _TableMetrics:
    row_count: int
    column_count: int
    populated_cell_count: int
    populated_cell_ratio: float
    meaningful_text_value_count: int
    numeric_or_date_value_count: int
    meaningful_column_ratio: float
    generic_header_ratio: float
    sentence_header_ratio: float
    numeric_type_consistency: float
    title_is_bad: bool
    title_is_unit_only: bool
    navigation_table: bool


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return _SPACE_RE.sub(" ", str(value).replace("\u00a0", " ")).strip()


def _canonical(value: Any) -> str:
    return " ".join(_WORD_RE.findall(_clean(value))).casefold()


def _is_populated(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(_clean(value).strip("-–—"))
    return True


def _looks_numeric_or_date(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return not isinstance(value, float) or math.isfinite(value)
    if isinstance(value, (date, datetime)):
        return True

    text = _clean(value).strip(".,;:")
    if not text:
        return False
    if _DATE_RE.fullmatch(text):
        return True
    text = text.replace("−", "-").replace("₹", "").replace("$", "")
    text = text.replace("€", "").replace("£", "").strip()
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1].strip()}"
    return bool(_NUMBER_RE.fullmatch(text))


def _is_meaningful_text(value: Any) -> bool:
    if not _is_populated(value) or _looks_numeric_or_date(value):
        return False
    text = _clean(value)
    if _CONTEXT_CELL_RE.fullmatch(text):
        return False
    words = _WORD_RE.findall(text)
    if not words or all(len(word) == 1 for word in words):
        return False
    return len("".join(words)) >= 2


def _is_generic_header(label: str) -> bool:
    return bool(_GENERIC_HEADER_RE.fullmatch(_clean(label)))


def _is_sentence_header(label: str) -> bool:
    text = _clean(label)
    return len(text) > 96 or len(_WORD_RE.findall(text)) >= 12


def _title_is_bad(title: str, *, max_length: int) -> bool:
    text = _clean(title)
    numeric_tokens = sum(
        _looks_numeric_or_date(token)
        for token in re.findall(r"[$₹€£]?\(?[+-]?[\d,.]+%?\)?", text)
    )
    currency_tokens = len(re.findall(r"[$₹€£]", text))
    return (
        not text
        or len(text) > max_length
        or bool(_DOT_LEADER_RE.search(text))
        or (numeric_tokens >= 4 and currency_tokens >= 2)
    )


def _truncate_title(value: str, max_length: int) -> str:
    text = _clean(value).strip(" .,:;-")
    if len(text) <= max_length:
        return text
    shortened = text[: max_length + 1].rsplit(" ", 1)[0].strip(" .,:;-")
    shortened = re.sub(
        r"\s+(?:a|an|and|at|for|from|in|of|or|the|to|with)$",
        "",
        shortened,
        flags=re.IGNORECASE,
    )
    return shortened or text[:max_length].strip(" .,:;-")


def _fallback_title(table: StructuredTable, *, max_length: int) -> str:
    descriptor = next(
        (
            _clean(row.get(column.key))
            for row in table.rows
            for column in table.columns
            if _is_meaningful_text(row.get(column.key))
        ),
        "",
    )
    descriptor = _truncate_title(descriptor, min(110, max_length - 24))

    years: list[str] = []
    for column in table.columns:
        for year in re.findall(r"\b(?:19|20)\d{2}\b", column.label):
            if year not in years:
                years.append(year)
    if descriptor and years:
        return _truncate_title(
            f"{descriptor} by {', '.join(years[:4])}", max_length
        )
    if descriptor:
        return _truncate_title(descriptor, max_length)

    labels = [
        _clean(column.label)
        for column in table.columns
        if not _is_generic_header(column.label)
        and not _is_sentence_header(column.label)
        and not _UNIT_ONLY_RE.fullmatch(_clean(column.label))
    ][:3]
    scope = ", ".join(labels)
    fallback = (
        f"Table on page {table.page_start}: {scope}"
        if scope
        else f"Structured table on page {table.page_start}"
    )
    return _truncate_title(fallback, max_length)


def _repair_title(
    table: StructuredTable, thresholds: TableValidationThresholds
) -> None:
    title = _clean(table.title)
    unit_only = bool(_UNIT_ONLY_RE.fullmatch(title))
    has_row_signals = bool(_DOT_LEADER_RE.search(title)) or len(
        re.findall(r"[$₹€£]", title)
    ) >= 2
    if (
        not unit_only
        and not has_row_signals
        and len(title) <= thresholds.max_title_length
    ):
        return

    if (
        len(title) > thresholds.max_title_length
        and not unit_only
        and not has_row_signals
    ):
        repaired = re.sub(
            r"\s+(?:were|was|are|is)?\s*as\s+follows\s*:?$",
            "",
            title,
            flags=re.IGNORECASE,
        )
        repaired = _truncate_title(repaired, thresholds.max_title_length)
    else:
        repaired = _fallback_title(table, max_length=thresholds.max_title_length)
    if not repaired or repaired == table.title:
        return

    table.title = repaired
    if table.deterministic_summary:
        _, separator, remainder = table.deterministic_summary.partition("\n")
        table.deterministic_summary = (
            f"Table: {repaired}{separator}{remainder}"
            if separator
            else f"Table: {repaired}"
        )


def _is_navigation_table(table: StructuredTable) -> bool:
    title = _canonical(table.title)
    if _NAVIGATION_TITLE_RE.fullmatch(title):
        return True
    if (
        "table of contents" in title
        or "index to exhibits" in title
        or "exhibits and financial statement schedules" in title
    ):
        return True

    labels = {_canonical(column.label) for column in table.columns}
    if {"date", "signature", "title"}.issubset(labels):
        return True
    has_exhibit_number = any(
        label in {"exhibit", "exhibit no", "exhibit number"} for label in labels
    )
    has_reference_description = any(
        label in {"description", "document description", "exhibit description"}
        for label in labels
    )
    return has_exhibit_number and has_reference_description


def _measure_table(
    table: StructuredTable, thresholds: TableValidationThresholds
) -> _TableMetrics:
    row_count = len(table.rows)
    column_count = len(table.columns)
    expected_cells = row_count * column_count
    values = [
        row.get(column.key)
        for row in table.rows
        for column in table.columns
    ]
    populated = [value for value in values if _is_populated(value)]
    numeric_or_date_count = sum(_looks_numeric_or_date(value) for value in populated)
    meaningful_text_count = sum(_is_meaningful_text(value) for value in populated)

    generic_headers = sum(
        _is_generic_header(column.label) for column in table.columns
    )
    sentence_headers = sum(
        _is_sentence_header(column.label) for column in table.columns
    )
    meaningful_headers = sum(
        not _is_generic_header(column.label)
        and not _is_sentence_header(column.label)
        and bool(_canonical(column.label))
        for column in table.columns
    )

    numeric_columns = [column for column in table.columns if column.type == "number"]
    numeric_matches = 0
    numeric_values = 0
    for column in numeric_columns:
        for row in table.rows:
            value = row.get(column.key)
            if not _is_populated(value):
                continue
            numeric_values += 1
            numeric_matches += _looks_numeric_or_date(value)
    numeric_consistency = (
        numeric_matches / numeric_values
        if numeric_values
        else min(
            1.0,
            (numeric_or_date_count + meaningful_text_count)
            / max(1, len(populated)),
        )
    )

    return _TableMetrics(
        row_count=row_count,
        column_count=column_count,
        populated_cell_count=len(populated),
        populated_cell_ratio=(
            len(populated) / expected_cells if expected_cells else 0.0
        ),
        meaningful_text_value_count=meaningful_text_count,
        numeric_or_date_value_count=numeric_or_date_count,
        meaningful_column_ratio=(
            meaningful_headers / column_count if column_count else 0.0
        ),
        generic_header_ratio=(
            generic_headers / column_count if column_count else 0.0
        ),
        sentence_header_ratio=(
            sentence_headers / column_count if column_count else 0.0
        ),
        numeric_type_consistency=numeric_consistency,
        title_is_bad=_title_is_bad(
            table.title, max_length=thresholds.max_title_length
        ),
        title_is_unit_only=bool(_UNIT_ONLY_RE.fullmatch(_clean(table.title))),
        navigation_table=_is_navigation_table(table),
    )


def _quality_score(
    metrics: _TableMetrics, thresholds: TableValidationThresholds
) -> float:
    structure = 1.0 if metrics.row_count >= 2 else 0.55
    if metrics.column_count > 20:
        structure *= 0.75
    density = min(1.0, metrics.populated_cell_ratio / 0.70)
    header_quality = metrics.meaningful_column_ratio * (
        1.0 - (0.5 * metrics.sentence_header_ratio)
    )
    title_quality = (
        0.0
        if metrics.title_is_bad
        else 0.35 if metrics.title_is_unit_only else 1.0
    )
    content_quality = min(
        1.0,
        (
            metrics.numeric_or_date_value_count
            + metrics.meaningful_text_value_count
        )
        / max(1, metrics.populated_cell_count),
    )
    score = (
        (0.25 * structure)
        + (0.20 * density)
        + (0.20 * header_quality)
        + (0.15 * metrics.numeric_type_consistency)
        + (0.10 * title_quality)
        + (0.10 * content_quality)
    )

    if metrics.generic_header_ratio >= 0.40:
        score -= 0.20
    if metrics.sentence_header_ratio >= 0.40 or (
        metrics.row_count == 1 and metrics.sentence_header_ratio > 0
    ):
        score -= 0.15
    if metrics.title_is_bad:
        score -= 0.10
    if metrics.populated_cell_ratio < thresholds.sparse_cell_ratio:
        score -= 0.20
    if metrics.column_count > 20:
        score -= 0.15
    return round(min(1.0, max(0.0, score)), 4)


def assess_table_quality(
    table: StructuredTable,
    *,
    thresholds: TableValidationThresholds | None = None,
) -> TableQualityAssessment:
    """Classify one normalized table before any LLM or embedding call."""
    selected = thresholds or TableValidationThresholds.from_env()
    metrics = _measure_table(table, selected)
    score = _quality_score(metrics, selected)
    reasons: list[str] = []

    if metrics.navigation_table:
        reasons.append("navigation_or_reference_table")
    if metrics.row_count == 0:
        reasons.append("no_data_rows")
    if metrics.column_count < 2:
        reasons.append("fewer_than_two_columns")
    if metrics.row_count == 1:
        reasons.append("single_row")
    if metrics.generic_header_ratio >= 0.40:
        reasons.append("generic_headers")
    if metrics.sentence_header_ratio >= 0.40 or (
        metrics.row_count == 1 and metrics.sentence_header_ratio > 0
    ):
        reasons.append("sentence_used_as_header")
    if metrics.populated_cell_ratio < selected.sparse_cell_ratio:
        reasons.append("sparse_cells")
    if metrics.numeric_or_date_value_count == 0:
        reasons.append("no_numeric_or_date_values")
    if metrics.title_is_bad:
        reasons.append("title_looks_like_table_content")
    elif metrics.title_is_unit_only:
        reasons.append("unit_only_title")
    if metrics.column_count > 20:
        reasons.append("unexpectedly_wide_table")

    structurally_impossible = metrics.row_count == 0 or metrics.column_count < 2
    if metrics.navigation_table or structurally_impossible:
        status: ValidationStatus = "rejected"
    elif table.extraction_method == "docling":
        clearly_broken_single_row = metrics.row_count == 1 and (
            (
                metrics.generic_header_ratio >= 0.50
                and metrics.numeric_or_date_value_count == 0
            )
            or (
                metrics.sentence_header_ratio >= 0.50
                and metrics.meaningful_text_value_count < 2
            )
            or (
                metrics.title_is_bad
                and metrics.meaningful_column_ratio < 0.67
            )
        )
        reliable_multi_row_structure = (
            metrics.row_count >= 2
            and metrics.populated_cell_ratio >= 0.50
            and (
                metrics.numeric_or_date_value_count
                + metrics.meaningful_text_value_count
            )
            >= metrics.row_count
        )
        status = (
            "accepted"
            if not clearly_broken_single_row
            and (
                score >= selected.docling_accept_score
                or reliable_multi_row_structure
            )
            else "rejected"
        )
    else:
        # Low-confidence PyMuPDF fragments are recoverable candidates. They are
        # quarantined instead of stored so the selective Docling pass can retry
        # their pages without polluting MongoDB or Qdrant.
        status = (
            "accepted"
            if score >= selected.pymupdf_accept_score
            else "quarantined"
        )

    if not reasons:
        reasons.append("quality_checks_passed")
    return TableQualityAssessment(
        table_id=table.table_id,
        extraction_method=table.extraction_method,
        status=status,
        score=score,
        reasons=tuple(reasons),
        populated_cell_ratio=round(metrics.populated_cell_ratio, 4),
        meaningful_column_ratio=round(metrics.meaningful_column_ratio, 4),
        generic_header_ratio=round(metrics.generic_header_ratio, 4),
        numeric_or_date_value_count=metrics.numeric_or_date_value_count,
    )


def validate_tables(
    tables: Sequence[StructuredTable],
    *,
    thresholds: TableValidationThresholds | None = None,
) -> TableValidationResult:
    """Partition tables while preserving their extraction order."""
    selected = thresholds or TableValidationThresholds.from_env()
    result = TableValidationResult()
    destinations = {
        "accepted": result.accepted,
        "quarantined": result.quarantined,
        "rejected": result.rejected,
    }
    for table in tables:
        assessment = assess_table_quality(table, thresholds=selected)
        result.assessments.append(assessment)
        if assessment.status == "accepted":
            _repair_title(table, selected)
        destinations[assessment.status].append(table)
    return result
