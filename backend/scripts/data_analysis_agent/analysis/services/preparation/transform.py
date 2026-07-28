from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from db.models.structured_table import StructuredTable

from ...models import (
    DatasetProfile,
    HydratedDatasetReference,
    MaterializationType,
    ProfiledDataType,
    TransformationOperation,
    TransformationSummary,
)
from ..profiling.inference import is_missing, normalize_text, parse_period
from ..profiling.rules import FOOTNOTE_RE, TOTAL_RE
from ..units import resolved_row_unit, table_unit_hint
from .recipe import CleaningRecipe


_NUMERIC_TYPES = frozenset(
    {ProfiledDataType.INTEGER, ProfiledDataType.NUMBER}
)
_PERIOD_TYPES = frozenset(
    {
        ProfiledDataType.CALENDAR_YEAR,
        ProfiledDataType.FISCAL_PERIOD,
        ProfiledDataType.QUARTER,
        ProfiledDataType.MONTH,
        ProfiledDataType.DATE,
    }
)
_NUMERIC_DECORATION_RE = re.compile(
    r"(?:us\$|[$€£₹%]|\b(?:usd|eur|gbp|inr|thousands?|millions?|"
    r"billions?|crores?|lakhs?|mn|bn|k)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PreparedRowLineage:
    output_row_index: int
    source_row_index: int
    source_page: int
    source_column_key: str | None = None


@dataclass(frozen=True, slots=True)
class ExcludedSourceRow:
    source_row_index: int
    reason: str
    duplicate_of_source_row_index: int | None = None


@dataclass(frozen=True, slots=True)
class SeparatedFootnote:
    source_row_index: int
    page: int
    text: str
    note_type: str


@dataclass(frozen=True, slots=True)
class TransformOutput:
    rows: tuple[dict[str, Any], ...]
    lineage: tuple[PreparedRowLineage, ...]
    excluded_rows: tuple[ExcludedSourceRow, ...]
    footnotes: tuple[SeparatedFootnote, ...]
    retained_source_row_count: int
    duplicate_row_count: int
    repeated_header_row_count: int
    total_or_subtotal_row_count: int
    numeric_parse_failure_count: int
    period_parse_failure_count: int
    quality_score_after: float
    transformations: tuple[TransformationSummary, ...]
    validation_checks: tuple[str, ...]


def _canonical_decimal(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        number = Decimal(str(value))
    elif isinstance(value, Decimal):
        number = value
    elif isinstance(value, str):
        text = normalize_text(value).replace("−", "-")
        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1].strip()
        text = _NUMERIC_DECORATION_RE.sub("", text).replace(" ", "")
        if "," in text and "." in text:
            decimal_separator = "," if text.rfind(",") > text.rfind(".") else "."
            grouping_separator = "." if decimal_separator == "," else ","
            text = text.replace(grouping_separator, "").replace(
                decimal_separator,
                ".",
            )
        elif "," in text:
            pieces = text.split(",")
            if len(pieces) == 2 and len(pieces[1]) in {1, 2}:
                text = ".".join(pieces)
            else:
                text = "".join(pieces)
        elif text.count(".") > 1:
            pieces = text.split(".")
            text = (
                "".join(pieces)
                if all(len(piece) == 3 for piece in pieces[1:])
                else "".join(pieces[:-1]) + "." + pieces[-1]
            )
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
        if negative:
            number = -number
    else:
        return None
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _row_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    return " ".join(
        normalize_text(row.get(key))
        for key in keys
        if not is_missing(row.get(key))
    )


def _is_repeated_header(
    row: dict[str, Any],
    table: StructuredTable,
) -> bool:
    present = 0
    matched = 0
    for column in table.columns:
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


def _row_digest(row: dict[str, Any]) -> str:
    encoded = json.dumps(
        row,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _note_type(text: str) -> str:
    lowered = text.casefold()
    if re.search(
        r"(?:[$€£₹%]|\b(?:usd|eur|gbp|inr|thousand|million|billion|"
        r"crore|lakh)\b)",
        lowered,
    ):
        return "unit"
    if re.search(r"\b(?:excluding|including|except|scope)\b", lowered):
        return "scope"
    if lowered.startswith("source"):
        return "source"
    if lowered.startswith(("note", "notes")):
        return "definition"
    return "other"


def _row_type(text: str) -> str:
    lowered = text.casefold().strip()
    if lowered.startswith(("subtotal", "sub total")):
        return "subtotal"
    if lowered.startswith(("total", "grand total")) or TOTAL_RE.search(text):
        return "total"
    return "observation"


def _quality_after(
    profile: DatasetProfile,
    *,
    duplicates: int,
    headers: int,
    footnotes: int,
    reshaped: bool,
) -> float:
    gain = 0.0
    gain += 0.05 if duplicates else 0.0
    gain += 0.04 if headers else 0.0
    gain += 0.04 if footnotes else 0.0
    gain += 0.05 if reshaped else 0.0
    return round(min(1.0, profile.quality_score + gain), 4)


class DeterministicDatasetTransformer:
    """Execute only allow-listed, reversible dataset preparation operations."""

    def transform(
        self,
        *,
        dataset: HydratedDatasetReference,
        profile: DatasetProfile,
        table: StructuredTable,
        recipe: CleaningRecipe,
    ) -> TransformOutput:
        if recipe.materialization == MaterializationType.SOURCE_PASSTHROUGH:
            return TransformOutput(
                rows=(),
                lineage=(),
                excluded_rows=(),
                footnotes=(),
                retained_source_row_count=len(table.rows),
                duplicate_row_count=0,
                repeated_header_row_count=0,
                total_or_subtotal_row_count=0,
                numeric_parse_failure_count=0,
                period_parse_failure_count=0,
                quality_score_after=profile.quality_score,
                transformations=(),
                validation_checks=(
                    "source_version_verified",
                    "schema_keys_unique",
                    "source_passthrough_no_copy",
                ),
            )

        column_keys = tuple(column.key for column in table.columns)
        if len(column_keys) != len(set(column_keys)):
            raise ValueError("source column keys are not unique")
        profiles_by_key = {item.key: item for item in profile.columns}
        columns_by_key = {item.key: item for item in table.columns}
        inferred_table_unit = table_unit_hint(
            table.title,
            *(column.label for column in table.columns),
        )
        retained: list[tuple[int, dict[str, Any], str]] = []
        excluded: list[ExcludedSourceRow] = []
        footnotes: list[SeparatedFootnote] = []
        seen_rows: dict[str, int] = {}
        duplicate_count = 0
        header_count = 0
        total_count = 0
        operation_counts: dict[TransformationOperation, int] = {
            operation: 0 for operation in TransformationOperation
        }

        for source_index, row in enumerate(table.rows):
            text = _row_text(row, column_keys)
            if _is_repeated_header(row, table):
                header_count += 1
                operation_counts[
                    TransformationOperation.REMOVE_REPEATED_HEADERS
                ] += 1
                excluded.append(
                    ExcludedSourceRow(
                        source_row_index=source_index,
                        reason="repeated_header",
                    )
                )
                continue
            if text and FOOTNOTE_RE.search(text):
                operation_counts[
                    TransformationOperation.SEPARATE_FOOTNOTES
                ] += 1
                footnotes.append(
                    SeparatedFootnote(
                        source_row_index=source_index,
                        page=dataset.page_start,
                        text=text,
                        note_type=_note_type(text),
                    )
                )
                excluded.append(
                    ExcludedSourceRow(
                        source_row_index=source_index,
                        reason="footnote",
                    )
                )
                continue
            digest = _row_digest(row)
            if digest in seen_rows:
                duplicate_count += 1
                operation_counts[
                    TransformationOperation.REMOVE_EXACT_DUPLICATES
                ] += 1
                excluded.append(
                    ExcludedSourceRow(
                        source_row_index=source_index,
                        reason="exact_duplicate",
                        duplicate_of_source_row_index=seen_rows[digest],
                    )
                )
                continue
            seen_rows[digest] = source_index
            classification = _row_type(text)
            if classification in {"total", "subtotal"}:
                total_count += 1
                operation_counts[
                    TransformationOperation.CLASSIFY_TOTAL_ROWS
                ] += 1
            retained.append((source_index, row, classification))

        numeric_failures = 0
        period_failures = 0
        output_rows: list[dict[str, Any]] = []
        lineage: list[PreparedRowLineage] = []
        output_keys = tuple(item.key for item in recipe.output_columns)
        reshape = bool(
            {
                TransformationOperation.RESHAPE_WIDE_TO_LONG,
                TransformationOperation.RESHAPE_TRANSPOSED_TO_LONG,
                TransformationOperation.RESHAPE_MATRIX_TO_LONG,
            }
            & {item.operation for item in recipe.transformations}
        )

        def normalized_value(key: str, value: Any) -> Any:
            nonlocal numeric_failures, period_failures
            if is_missing(value):
                operation_counts[
                    TransformationOperation.NORMALIZE_MISSING
                ] += 1
                return None
            column_profile = profiles_by_key[key]
            if (
                column_profile.inferred_type in _NUMERIC_TYPES
                or (
                    column_profile.inferred_type == ProfiledDataType.MIXED
                    and column_profile.declared_type == "number"
                )
            ):
                parsed = _canonical_decimal(value)
                if parsed is None:
                    numeric_failures += 1
                    return normalize_text(value)
                operation_counts[TransformationOperation.PARSE_NUMERIC] += 1
                return parsed
            if column_profile.inferred_type in _PERIOD_TYPES:
                parsed_period = parse_period(value, label=column_profile.label)
                if parsed_period is None:
                    period_failures += 1
                    return normalize_text(value)
                operation_counts[TransformationOperation.PARSE_PERIOD] += 1
                return parsed_period.label
            if column_profile.inferred_type == ProfiledDataType.BOOLEAN:
                if isinstance(value, bool):
                    return value
                lowered = normalize_text(value).casefold()
                return True if lowered in {"true", "yes", "y"} else False
            normalized = normalize_text(value)
            if normalized != str(value):
                operation_counts[TransformationOperation.NORMALIZE_TEXT] += 1
            return normalized

        if reshape:
            series_key, value_key, unit_key = output_keys[-3:]
            dimension_keys = recipe.dimension_column_keys
            value_keys = recipe.value_column_keys
            for source_index, row, classification in retained:
                dimensions = {
                    key: normalized_value(key, row.get(key))
                    for key in dimension_keys
                }
                row_context = _row_text(row, dimension_keys)
                for source_column_key in value_keys:
                    source_column = columns_by_key[source_column_key]
                    source_profile = profiles_by_key[source_column_key]
                    series_value = source_column.label
                    if source_column_key in recipe.period_column_keys:
                        parsed_period = parse_period(
                            source_column.label,
                            label=source_column.label,
                        )
                        if parsed_period is None:
                            period_failures += 1
                        else:
                            series_value = parsed_period.label
                            operation_counts[
                                TransformationOperation.PARSE_PERIOD
                            ] += 1
                    output = {
                        **dimensions,
                        series_key: series_value,
                        value_key: normalized_value(
                            source_column_key,
                            row.get(source_column_key),
                        ),
                        unit_key: resolved_row_unit(
                            column_unit=(
                                source_profile.detected_unit
                                or source_column.unit
                            ),
                            table_hint=inferred_table_unit,
                            row_text=row_context,
                        ),
                    }
                    if recipe.row_type_column_key:
                        output[recipe.row_type_column_key] = classification
                    output_index = len(output_rows)
                    output_rows.append(output)
                    lineage.append(
                        PreparedRowLineage(
                            output_row_index=output_index,
                            source_row_index=source_index,
                            source_page=dataset.page_start,
                            source_column_key=source_column_key,
                        )
                    )
            reshape_operation = next(
                item.operation
                for item in recipe.transformations
                if item.operation
                in {
                    TransformationOperation.RESHAPE_WIDE_TO_LONG,
                    TransformationOperation.RESHAPE_TRANSPOSED_TO_LONG,
                    TransformationOperation.RESHAPE_MATRIX_TO_LONG,
                }
            )
            operation_counts[reshape_operation] = len(output_rows)
        else:
            for source_index, row, classification in retained:
                output = {
                    column.key: normalized_value(
                        column.key,
                        row.get(column.key),
                    )
                    for column in table.columns
                }
                if recipe.row_type_column_key:
                    output[recipe.row_type_column_key] = classification
                output_index = len(output_rows)
                output_rows.append(output)
                lineage.append(
                    PreparedRowLineage(
                        output_row_index=output_index,
                        source_row_index=source_index,
                        source_page=dataset.page_start,
                    )
                )

        accounted = {
            source_index for source_index, _row, _classification in retained
        } | {item.source_row_index for item in excluded}
        if accounted != set(range(len(table.rows))):
            raise ValueError("not every source row was accounted for")
        if len(lineage) != len(output_rows):
            raise ValueError("every output row requires lineage")
        if any(set(row) != set(output_keys) for row in output_rows):
            raise ValueError("output rows do not match the normalized schema")
        if numeric_failures or period_failures:
            raise ValueError(
                "normalization encountered values that violate the profiled type"
            )

        transformations = tuple(
            item.model_copy(
                update={
                    "affected_row_count": operation_counts[item.operation],
                }
            )
            for item in recipe.transformations
        )
        return TransformOutput(
            rows=tuple(output_rows),
            lineage=tuple(lineage),
            excluded_rows=tuple(excluded),
            footnotes=tuple(footnotes),
            retained_source_row_count=len(retained),
            duplicate_row_count=duplicate_count,
            repeated_header_row_count=header_count,
            total_or_subtotal_row_count=total_count,
            numeric_parse_failure_count=numeric_failures,
            period_parse_failure_count=period_failures,
            quality_score_after=_quality_after(
                profile,
                duplicates=duplicate_count,
                headers=header_count,
                footnotes=len(footnotes),
                reshaped=reshape,
            ),
            transformations=transformations,
            validation_checks=(
                "source_version_verified",
                "schema_keys_unique",
                "all_source_rows_accounted",
                "all_output_rows_have_lineage",
                "numeric_values_parsed",
                "period_values_parsed",
            ),
        )
