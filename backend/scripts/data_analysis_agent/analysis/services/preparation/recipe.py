from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from ...models import (
    DATASET_NORMALIZER_VERSION,
    DatasetProfile,
    HydratedDatasetReference,
    MaterializationType,
    NormalizedColumn,
    NormalizedDataType,
    ProfiledDataType,
    SemanticRole,
    TableOrientation,
    TransformationOperation,
    TransformationSummary,
)
from ..profiling.inference import parse_period


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
_NUMERIC_TEXT_NORMALIZATION_RE = re.compile(
    r"(?:[$€£₹%()]|[−–—]|\d[.,]\d{3}(?:\D|$)|"
    r"\b(?:usd|eur|gbp|inr|thousands?|millions?|billions?|"
    r"crores?|lakhs?|mn|bn)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CleaningRecipe:
    recipe_hash: str
    materialization: MaterializationType
    transformations: tuple[TransformationSummary, ...]
    output_columns: tuple[NormalizedColumn, ...]
    dimension_column_keys: tuple[str, ...] = ()
    value_column_keys: tuple[str, ...] = ()
    period_column_keys: tuple[str, ...] = ()
    row_type_column_key: str | None = None


def _normalized_type(value: ProfiledDataType) -> NormalizedDataType:
    return {
        ProfiledDataType.BOOLEAN: NormalizedDataType.BOOLEAN,
        ProfiledDataType.INTEGER: NormalizedDataType.INTEGER,
        ProfiledDataType.NUMBER: NormalizedDataType.DECIMAL,
        ProfiledDataType.CALENDAR_YEAR: NormalizedDataType.PERIOD,
        ProfiledDataType.FISCAL_PERIOD: NormalizedDataType.PERIOD,
        ProfiledDataType.QUARTER: NormalizedDataType.PERIOD,
        ProfiledDataType.MONTH: NormalizedDataType.PERIOD,
        ProfiledDataType.DATE: NormalizedDataType.DATE,
        ProfiledDataType.STRING: NormalizedDataType.STRING,
        ProfiledDataType.MIXED: NormalizedDataType.UNKNOWN,
        ProfiledDataType.EMPTY: NormalizedDataType.UNKNOWN,
    }[value]


def _stable_transformation(
    operation: TransformationOperation,
    *,
    input_columns: tuple[str, ...] = (),
    reason: str,
    confidence: float = 1.0,
    reversible: bool = True,
) -> TransformationSummary:
    payload = {
        "operation": operation.value,
        "input_columns": input_columns,
        "reason": reason,
        "confidence": round(confidence, 4),
        "reversible": reversible,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    identifier = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    return TransformationSummary(
        transformation_id=f"transform_{identifier}",
        operation=operation,
        input_columns=input_columns,
        reason=reason,
        confidence=confidence,
        reversible=reversible,
    )


def _unique_output_key(existing: set[str], preferred: str) -> str:
    value = preferred
    suffix = 2
    while value in existing:
        value = f"{preferred}_{suffix}"
        suffix += 1
    existing.add(value)
    return value


def _source_columns(
    dataset: HydratedDatasetReference,
    profile: DatasetProfile,
) -> tuple[NormalizedColumn, ...]:
    profiles = {item.key: item for item in profile.columns}
    return tuple(
        NormalizedColumn(
            key=column.key,
            label=column.label,
            data_type=(
                NormalizedDataType.DECIMAL
                if (
                    profiles[column.key].inferred_type
                    == ProfiledDataType.MIXED
                    and profiles[column.key].declared_type == "number"
                )
                else NormalizedDataType.STRING
                if (
                    profiles[column.key].inferred_type
                    == ProfiledDataType.MIXED
                    and profiles[column.key].declared_type == "string"
                )
                else _normalized_type(profiles[column.key].inferred_type)
            ),
            unit=profiles[column.key].detected_unit,
            semantic_role=profiles[column.key].semantic_role,
            source_column_keys=(column.key,),
        )
        for column in dataset.columns
        if column.key in profiles
    )


def _long_output_columns(
    *,
    source_columns: tuple[NormalizedColumn, ...],
    dimension_keys: tuple[str, ...],
    value_keys: tuple[str, ...],
    period_keys: tuple[str, ...],
    series_label: str,
    value_data_type: NormalizedDataType,
    row_type_key: str | None,
) -> tuple[NormalizedColumn, ...]:
    source_by_key = {item.key: item for item in source_columns}
    output = [source_by_key[key] for key in dimension_keys]
    used = {item.key for item in output}
    if row_type_key is not None:
        used.add(row_type_key)
        output.append(
            NormalizedColumn(
                key=row_type_key,
                label="Row type",
                data_type=NormalizedDataType.STRING,
                semantic_role=SemanticRole.DIMENSION,
            )
        )
    series_key = _unique_output_key(
        used,
        "__period" if period_keys else "__series",
    )
    value_key = _unique_output_key(used, "__value")
    unit_key = _unique_output_key(used, "__unit")
    output.extend(
        (
            NormalizedColumn(
                key=series_key,
                label=series_label,
                data_type=(
                    NormalizedDataType.PERIOD
                    if period_keys
                    else NormalizedDataType.STRING
                ),
                semantic_role=(
                    SemanticRole.TIME_PERIOD
                    if period_keys
                    else SemanticRole.DIMENSION
                ),
                source_column_keys=period_keys or value_keys,
            ),
            NormalizedColumn(
                key=value_key,
                label="Value",
                data_type=value_data_type,
                semantic_role=SemanticRole.METRIC,
                source_column_keys=value_keys,
            ),
            NormalizedColumn(
                key=unit_key,
                label="Unit",
                data_type=NormalizedDataType.STRING,
                semantic_role=SemanticRole.DIMENSION,
                source_column_keys=value_keys,
            ),
        )
    )
    return tuple(output)


def _recipe_hash(
    *,
    materialization: MaterializationType,
    transformations: tuple[TransformationSummary, ...],
    output_columns: tuple[NormalizedColumn, ...],
    dimensions: tuple[str, ...],
    values: tuple[str, ...],
    periods: tuple[str, ...],
    row_type_key: str | None,
) -> str:
    payload = {
        "normalizer_version": DATASET_NORMALIZER_VERSION,
        "materialization": materialization.value,
        "transformations": [
            item.model_dump(mode="json", exclude={"affected_row_count"})
            for item in transformations
        ],
        "output_columns": [
            item.model_dump(mode="json") for item in output_columns
        ],
        "dimensions": dimensions,
        "values": values,
        "periods": periods,
        "row_type_key": row_type_key,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def preparation_cache_key(
    *,
    dataset_id: str,
    source_version: str,
    recipe_hash: str,
) -> str:
    payload = (
        f"{dataset_id}\x1f{source_version}\x1f"
        f"{DATASET_NORMALIZER_VERSION}\x1f{recipe_hash}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalized_dataset_id(cache_key: str) -> str:
    return f"normalized_{cache_key[:24]}"


def build_cleaning_recipe(
    *,
    dataset: HydratedDatasetReference,
    profile: DatasetProfile,
) -> CleaningRecipe:
    """Create a safe, deterministic recipe solely from verified profile signals."""

    source_columns = _source_columns(dataset, profile)
    profiles_by_key = {item.key: item for item in profile.columns}
    transformations: list[TransformationSummary] = []
    dimension_keys: tuple[str, ...] = ()
    value_keys: tuple[str, ...] = ()
    period_keys: tuple[str, ...] = ()
    row_type_key: str | None = None
    if profile.total_or_subtotal_row_count:
        row_type_key = _unique_output_key(
            {item.key for item in source_columns},
            "__row_type",
        )

    if profile.duplicate_row_count:
        transformations.append(
            _stable_transformation(
                TransformationOperation.REMOVE_EXACT_DUPLICATES,
                reason="The profiler detected byte-equivalent duplicate rows.",
            )
        )
    if profile.repeated_header_row_count:
        transformations.append(
            _stable_transformation(
                TransformationOperation.REMOVE_REPEATED_HEADERS,
                reason="Repeated PDF header rows are not analytical observations.",
            )
        )
    if profile.footnote_like_row_count:
        transformations.append(
            _stable_transformation(
                TransformationOperation.SEPARATE_FOOTNOTES,
                reason="Footnote rows must remain available without entering calculations.",
            )
        )
    if profile.total_or_subtotal_row_count:
        transformations.append(
            _stable_transformation(
                TransformationOperation.CLASSIFY_TOTAL_ROWS,
                reason="Totals and subtotals must be retained but explicitly classified.",
            )
        )
    missing_keys = tuple(
        item.key for item in profile.columns if item.missing_count
    )
    if missing_keys:
        transformations.append(
            _stable_transformation(
                TransformationOperation.NORMALIZE_MISSING,
                input_columns=missing_keys,
                reason="Known missing markers are represented as null values.",
            )
        )
    numeric_normalization_keys = tuple(
        item.key
        for item in profile.columns
        if (
            item.inferred_type in _NUMERIC_TYPES
            or (
                item.inferred_type == ProfiledDataType.MIXED
                and item.declared_type == "number"
            )
        )
        and (
            item.declared_type != "number"
            or "declared_type_mismatch" in item.parsing_warnings
            or item.inferred_type == ProfiledDataType.MIXED
            or any(
                _NUMERIC_TEXT_NORMALIZATION_RE.search(value)
                for value in item.example_values
            )
        )
    )
    if numeric_normalization_keys:
        transformations.append(
            _stable_transformation(
                TransformationOperation.PARSE_NUMERIC,
                input_columns=numeric_normalization_keys,
                reason="Inferred numeric values must use deterministic decimal strings.",
            )
        )
    period_normalization_keys = tuple(
        item.key
        for item in profile.columns
        if item.inferred_type in _PERIOD_TYPES and item.declared_type != "date"
    )
    if period_normalization_keys:
        transformations.append(
            _stable_transformation(
                TransformationOperation.PARSE_PERIOD,
                input_columns=period_normalization_keys,
                reason="Detected periods must use canonical calendar or fiscal labels.",
            )
        )

    if profile.orientation == TableOrientation.WIDE_TIME_SERIES:
        period_keys = tuple(
            column.key
            for column in dataset.columns
            if parse_period(column.label, label=column.label) is not None
        )
        dimension_keys = tuple(
            column.key
            for column in dataset.columns
            if column.key not in period_keys
        )
        value_keys = period_keys
        if len(period_keys) >= 2 and dimension_keys:
            transformations.append(
                _stable_transformation(
                    TransformationOperation.RESHAPE_WIDE_TO_LONG,
                    input_columns=tuple(column.key for column in dataset.columns),
                    reason="Period headers are normalized into one period/value axis.",
                )
            )
    elif profile.orientation in {
        TableOrientation.TRANSPOSED,
        TableOrientation.MATRIX,
    }:
        dimension_keys = tuple(
            column.key
            for column in dataset.columns
            if profiles_by_key[column.key].inferred_type not in _NUMERIC_TYPES
        )
        value_keys = tuple(
            column.key
            for column in dataset.columns
            if profiles_by_key[column.key].inferred_type in _NUMERIC_TYPES
        )
        if dimension_keys and value_keys:
            operation = (
                TransformationOperation.RESHAPE_TRANSPOSED_TO_LONG
                if profile.orientation == TableOrientation.TRANSPOSED
                else TransformationOperation.RESHAPE_MATRIX_TO_LONG
            )
            transformations.append(
                _stable_transformation(
                    operation,
                    input_columns=tuple(column.key for column in dataset.columns),
                    reason="Metric rows and value columns are normalized into observations.",
                    confidence=0.96,
                )
            )

    materialized = bool(transformations)
    if materialized:
        string_keys = tuple(
            item.key
            for item in profile.columns
            if item.inferred_type
            in {ProfiledDataType.STRING, ProfiledDataType.MIXED}
        )
        if string_keys:
            transformations.append(
                _stable_transformation(
                    TransformationOperation.NORMALIZE_TEXT,
                    input_columns=string_keys,
                    reason="Whitespace is canonicalized while source values remain immutable.",
                )
            )
        numeric_keys = tuple(
            item.key
            for item in profile.columns
            if item.inferred_type in _NUMERIC_TYPES
            or (
                item.inferred_type == ProfiledDataType.MIXED
                and item.declared_type == "number"
            )
        )
        operations = {item.operation for item in transformations}
        if numeric_keys and TransformationOperation.PARSE_NUMERIC not in operations:
            transformations.append(
                _stable_transformation(
                    TransformationOperation.PARSE_NUMERIC,
                    input_columns=numeric_keys,
                    reason="Numeric observations use deterministic decimal strings.",
                )
            )
        temporal_keys = tuple(
            item.key
            for item in profile.columns
            if item.inferred_type in _PERIOD_TYPES
        )
        if (
            (temporal_keys or period_keys)
            and TransformationOperation.PARSE_PERIOD not in operations
        ):
            transformations.append(
                _stable_transformation(
                    TransformationOperation.PARSE_PERIOD,
                    input_columns=temporal_keys or period_keys,
                    reason="Calendar, fiscal and ranged periods use canonical labels.",
                )
            )

    if (
        period_keys
        and dimension_keys
        and TransformationOperation.RESHAPE_WIDE_TO_LONG
        in {item.operation for item in transformations}
    ):
        output_columns = _long_output_columns(
            source_columns=source_columns,
            dimension_keys=dimension_keys,
            value_keys=value_keys,
            period_keys=period_keys,
            series_label="Period",
            value_data_type=(
                NormalizedDataType.DECIMAL
                if all(
                    profiles_by_key[key].inferred_type in _NUMERIC_TYPES
                    for key in value_keys
                )
                else NormalizedDataType.UNKNOWN
            ),
            row_type_key=row_type_key,
        )
    elif (
        value_keys
        and dimension_keys
        and any(
            item.operation
            in {
                TransformationOperation.RESHAPE_TRANSPOSED_TO_LONG,
                TransformationOperation.RESHAPE_MATRIX_TO_LONG,
            }
            for item in transformations
        )
    ):
        output_columns = _long_output_columns(
            source_columns=source_columns,
            dimension_keys=dimension_keys,
            value_keys=value_keys,
            period_keys=(),
            series_label="Series",
            value_data_type=NormalizedDataType.DECIMAL,
            row_type_key=row_type_key,
        )
    else:
        output = list(source_columns)
        if row_type_key is not None:
            output.append(
                NormalizedColumn(
                    key=row_type_key,
                    label="Row type",
                    data_type=NormalizedDataType.STRING,
                )
            )
        output_columns = tuple(output)

    materialization = (
        MaterializationType.MATERIALIZED_DATASET
        if transformations
        else MaterializationType.SOURCE_PASSTHROUGH
    )
    transformations_tuple = tuple(transformations)
    recipe_hash = _recipe_hash(
        materialization=materialization,
        transformations=transformations_tuple,
        output_columns=output_columns,
        dimensions=dimension_keys,
        values=value_keys,
        periods=period_keys,
        row_type_key=row_type_key,
    )
    return CleaningRecipe(
        recipe_hash=recipe_hash,
        materialization=materialization,
        transformations=transformations_tuple,
        output_columns=output_columns,
        dimension_column_keys=dimension_keys,
        value_column_keys=value_keys,
        period_column_keys=period_keys,
        row_type_column_key=row_type_key,
    )
