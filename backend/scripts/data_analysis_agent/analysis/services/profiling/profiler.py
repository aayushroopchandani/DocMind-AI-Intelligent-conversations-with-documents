from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from db.models.structured_table import StructuredTable, TableColumn

from ...models import (
    DATASET_PROFILER_VERSION,
    ColumnProfile,
    DatasetProfile,
    HydratedDatasetReference,
    ProfiledDataType,
    SemanticRole,
    StringColumnStatistics,
)
from .inference import ValueObservation, detect_unit, observe_value
from .rules import ID_LABEL_RE, METRIC_LABEL_RE, NUMERIC_TYPES, TEMPORAL_TYPES
from .shape import analyze_rows, infer_orientation, score_quality
from .statistics import (
    declared_type_matches,
    infer_type,
    numeric_statistics,
    percentage,
    rounded,
    string_statistics,
    time_statistics,
)


def _semantic_role(
    *,
    column: TableColumn,
    inferred: ProfiledDataType,
    non_null_count: int,
    cardinality_ratio: float,
    string_profile: StringColumnStatistics | None,
) -> SemanticRole:
    label = f"{column.key} {column.label}"
    if inferred == ProfiledDataType.EMPTY:
        return SemanticRole.UNKNOWN
    if inferred == ProfiledDataType.BOOLEAN:
        return SemanticRole.BOOLEAN_FLAG
    if inferred in TEMPORAL_TYPES:
        return SemanticRole.TIME_PERIOD
    if ID_LABEL_RE.search(label):
        return SemanticRole.IDENTIFIER
    if inferred in NUMERIC_TYPES:
        return SemanticRole.METRIC
    if string_profile is not None:
        if string_profile.possible_identifier:
            return SemanticRole.IDENTIFIER
        if string_profile.average_text_length >= 80:
            return SemanticRole.FREE_TEXT
        if string_profile.low_cardinality:
            return SemanticRole.CATEGORY
    if METRIC_LABEL_RE.search(label) and non_null_count:
        return SemanticRole.METRIC
    if inferred == ProfiledDataType.STRING or cardinality_ratio < 1:
        return SemanticRole.DIMENSION
    return SemanticRole.UNKNOWN


def _column_profile(
    column: TableColumn,
    rows: Sequence[dict[str, Any]],
) -> ColumnProfile:
    observations: list[ValueObservation] = []
    examples: list[str] = []
    seen_examples: set[str] = set()
    unique_values: set[str] = set()
    for row in rows:
        observation = observe_value(row.get(column.key), label=column.label)
        if observation is None:
            continue
        observations.append(observation)
        unique_values.add(observation.canonical)
        if observation.canonical not in seen_examples and len(examples) < 3:
            seen_examples.add(observation.canonical)
            examples.append(observation.display)

    total_count = len(rows)
    non_null_count = len(observations)
    missing_count = total_count - non_null_count
    unique_count = len(unique_values)
    cardinality_ratio = unique_count / non_null_count if non_null_count else 0.0
    inferred, confidence = infer_type(observations)
    numeric_values = [
        float(item.numeric) for item in observations if item.numeric is not None
    ]
    string_profile = string_statistics(
        observations=observations,
        label=column.label,
        unique_count=unique_count,
    )
    parsing_warnings: list[str] = []
    if inferred == ProfiledDataType.MIXED:
        parsing_warnings.append("mixed_value_types")
    if not declared_type_matches(column.type, inferred):
        parsing_warnings.append("declared_type_mismatch")
    numeric_share = len(numeric_values) / non_null_count if non_null_count else 0.0
    temporal_share = (
        sum(item.period is not None for item in observations) / non_null_count
        if non_null_count
        else 0.0
    )
    if 0 < numeric_share < 0.90:
        parsing_warnings.append("partially_numeric")
    if 0 < temporal_share < 0.90:
        parsing_warnings.append("partially_temporal")

    role = _semantic_role(
        column=column,
        inferred=inferred,
        non_null_count=non_null_count,
        cardinality_ratio=cardinality_ratio,
        string_profile=string_profile,
    )
    return ColumnProfile(
        key=column.key,
        label=column.label,
        declared_type=column.type,
        inferred_type=inferred,
        semantic_role=role,
        total_count=total_count,
        non_null_count=non_null_count,
        missing_count=missing_count,
        missing_percentage=percentage(missing_count, total_count),
        unique_count=unique_count,
        cardinality_ratio=rounded(cardinality_ratio),
        example_values=tuple(examples),
        detected_unit=column.unit or detect_unit(
            column.label,
            *(item.display for item in observations[:5]),
        ),
        type_confidence=rounded(confidence),
        parsing_warnings=tuple(parsing_warnings),
        numeric_statistics=numeric_statistics(numeric_values),
        time_statistics=time_statistics(observations),
        string_statistics=string_profile,
    )


class DeterministicDatasetProfiler:
    """Produce reproducible structural and quality metadata without an LLM."""

    version = DATASET_PROFILER_VERSION

    def profile(
        self,
        dataset: HydratedDatasetReference,
        table: StructuredTable,
    ) -> DatasetProfile:
        columns = tuple(
            _column_profile(column, table.rows) for column in table.columns
        )
        row_features = analyze_rows(table)
        orientation, periods_in_headers = infer_orientation(
            table=table,
            profiles=columns,
            row_features=row_features,
        )
        quality_score, quality_warnings, suitable = score_quality(
            row_count=len(table.rows),
            profiles=columns,
            row_features=row_features,
            orientation=orientation,
        )
        return DatasetProfile(
            dataset_id=dataset.dataset_id,
            source_version=dataset.source_version,
            profiler_version=self.version,
            table_id=table.table_id,
            document_id=table.document_id,
            title=table.title,
            row_count=len(table.rows),
            column_count=len(table.columns),
            is_empty=not table.rows,
            duplicate_row_count=row_features.duplicate_count,
            repeated_header_row_count=row_features.repeated_header_count,
            total_or_subtotal_row_count=row_features.total_or_subtotal_count,
            footnote_like_row_count=row_features.footnote_like_count,
            periods_in_headers=periods_in_headers,
            periods_in_rows=any(
                column.time_statistics is not None for column in columns
            ),
            orientation=orientation,
            quality_score=quality_score,
            quality_warnings=quality_warnings,
            suitable_for_analysis=suitable,
            columns=columns,
        )
