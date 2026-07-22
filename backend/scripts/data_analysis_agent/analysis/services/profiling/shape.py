from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from db.models.structured_table import StructuredTable, TableColumn

from ...models import (
    ColumnProfile,
    ProfileQualityWarning,
    ProfiledDataType,
    SemanticRole,
    TableOrientation,
)
from .inference import is_missing, normalize_text, parse_period
from .rules import FOOTNOTE_RE, METRIC_LABEL_RE, NUMERIC_TYPES, TOTAL_RE
from .statistics import rounded


@dataclass(frozen=True, slots=True)
class RowFeatures:
    duplicate_count: int
    repeated_header_count: int
    total_or_subtotal_count: int
    footnote_like_count: int


def _row_digest(row: dict[str, Any]) -> bytes:
    encoded = json.dumps(
        row,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).digest()


def _duplicate_count(rows: Sequence[dict[str, Any]]) -> int:
    seen: set[bytes] = set()
    duplicates = 0
    for row in rows:
        digest = _row_digest(row)
        if digest in seen:
            duplicates += 1
        else:
            seen.add(digest)
    return duplicates


def _row_labels(row: dict[str, Any], columns: Sequence[TableColumn]) -> list[str]:
    return [
        normalize_text(row.get(column.key))
        for column in columns
        if not is_missing(row.get(column.key))
    ]


def _repeated_header_row(row: dict[str, Any], columns: Sequence[TableColumn]) -> bool:
    present = 0
    matched = 0
    for column in columns:
        value = row.get(column.key)
        if is_missing(value):
            continue
        present += 1
        normalized = normalize_text(value).casefold()
        if normalized in {
            normalize_text(column.key).casefold(),
            normalize_text(column.label).casefold(),
        }:
            matched += 1
    return present >= 2 and (matched / present) >= 0.60


def analyze_rows(table: StructuredTable) -> RowFeatures:
    repeated_headers = 0
    total_rows = 0
    footnote_rows = 0
    for row in table.rows:
        labels = _row_labels(row, table.columns)
        if _repeated_header_row(row, table.columns):
            repeated_headers += 1
        if any(TOTAL_RE.search(value) for value in labels):
            total_rows += 1
        if any(FOOTNOTE_RE.search(value) for value in labels):
            footnote_rows += 1
    return RowFeatures(
        duplicate_count=_duplicate_count(table.rows),
        repeated_header_count=repeated_headers,
        total_or_subtotal_count=total_rows,
        footnote_like_count=footnote_rows,
    )


def _metric_row_ratio(table: StructuredTable) -> float:
    if not table.rows or not table.columns:
        return 0.0
    first_key = table.columns[0].key
    populated = [
        normalize_text(row.get(first_key))
        for row in table.rows
        if not is_missing(row.get(first_key))
    ]
    if not populated:
        return 0.0
    return sum(bool(METRIC_LABEL_RE.search(value)) for value in populated) / len(
        populated
    )


def infer_orientation(
    *,
    table: StructuredTable,
    profiles: Sequence[ColumnProfile],
    row_features: RowFeatures,
) -> tuple[TableOrientation, bool]:
    period_header_count = sum(
        parse_period(column.label, label=column.label) is not None
        for column in table.columns
    )
    periods_in_headers = period_header_count >= 2
    numeric_columns = sum(
        profile.inferred_type in NUMERIC_TYPES for profile in profiles
    )
    textual_columns = sum(
        profile.inferred_type in {ProfiledDataType.STRING, ProfiledDataType.MIXED}
        for profile in profiles
    )
    if periods_in_headers:
        return TableOrientation.WIDE_TIME_SERIES, True
    if len(profiles) >= 3 and _metric_row_ratio(table) >= 0.50:
        return TableOrientation.TRANSPOSED, False
    if row_features.total_or_subtotal_count and len(table.rows) <= 15:
        return TableOrientation.SUMMARY, False
    if (
        len(profiles) == 2
        and _metric_row_ratio(table) >= 0.35
        and profiles[0].semantic_role
        in {
            SemanticRole.CATEGORY,
            SemanticRole.DIMENSION,
            SemanticRole.IDENTIFIER,
        }
    ):
        return TableOrientation.KEY_VALUE, False
    if numeric_columns == 0 and textual_columns == len(profiles):
        return TableOrientation.PRIMARILY_TEXTUAL, False
    if table.page_end > table.page_start or len(table.source_fragments) > 1:
        return TableOrientation.CONTINUATION, False
    metric_headers = sum(
        profile.inferred_type in NUMERIC_TYPES
        and bool(METRIC_LABEL_RE.search(profile.label))
        for profile in profiles
    )
    first_is_axis = bool(profiles) and profiles[0].semantic_role in {
        SemanticRole.CATEGORY,
        SemanticRole.DIMENSION,
        SemanticRole.IDENTIFIER,
    }
    if (
        len(profiles) >= 3
        and numeric_columns >= 2
        and first_is_axis
        and metric_headers < numeric_columns
    ):
        return TableOrientation.MATRIX, False
    if table.rows:
        return TableOrientation.ORDINARY_RECORDS, False
    return TableOrientation.UNKNOWN, False


def score_quality(
    *,
    row_count: int,
    profiles: Sequence[ColumnProfile],
    row_features: RowFeatures,
    orientation: TableOrientation,
) -> tuple[float, tuple[ProfileQualityWarning, ...], bool]:
    if row_count == 0:
        return 0.0, (ProfileQualityWarning.EMPTY_DATASET,), False

    warnings: list[ProfileQualityWarning] = []
    score = 1.0
    total_cells = row_count * len(profiles)
    missing_cells = sum(profile.missing_count for profile in profiles)
    missing_ratio = missing_cells / total_cells if total_cells else 1.0
    if missing_ratio >= 0.30:
        warnings.append(ProfileQualityWarning.HIGH_MISSINGNESS)
        score -= min(0.35, missing_ratio * 0.40)
    if row_features.duplicate_count:
        warnings.append(ProfileQualityWarning.DUPLICATE_ROWS)
        score -= min(0.20, (row_features.duplicate_count / row_count) * 0.25)
    mixed_count = sum(
        profile.inferred_type == ProfiledDataType.MIXED for profile in profiles
    )
    if mixed_count:
        warnings.append(ProfileQualityWarning.MIXED_COLUMN_TYPES)
        score -= min(0.20, (mixed_count / len(profiles)) * 0.20)
    mismatch_count = sum(
        "declared_type_mismatch" in profile.parsing_warnings for profile in profiles
    )
    if mismatch_count:
        warnings.append(ProfileQualityWarning.DECLARED_TYPE_MISMATCH)
        score -= min(0.15, (mismatch_count / len(profiles)) * 0.15)
    if row_features.repeated_header_count:
        warnings.append(ProfileQualityWarning.REPEATED_HEADER_ROWS)
        score -= min(
            0.10,
            (row_features.repeated_header_count / row_count) * 0.15,
        )
    informative = [profile for profile in profiles if profile.non_null_count]
    if not informative:
        warnings.append(ProfileQualityWarning.LOW_INFORMATION)
        score -= 0.50
    if orientation == TableOrientation.PRIMARILY_TEXTUAL:
        warnings.append(ProfileQualityWarning.PRIMARILY_TEXTUAL)
        score -= 0.10
    final_score = max(0.0, min(1.0, score))
    suitable = bool(informative) and final_score >= 0.45
    return rounded(final_score), tuple(warnings), suitable
