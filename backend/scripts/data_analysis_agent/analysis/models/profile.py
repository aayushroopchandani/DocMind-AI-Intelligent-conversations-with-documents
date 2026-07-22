from __future__ import annotations

import hashlib
from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


DATASET_PROFILER_VERSION = "1.0.0"


class ProfiledDataType(str, Enum):
    EMPTY = "empty"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    CALENDAR_YEAR = "calendar_year"
    FISCAL_PERIOD = "fiscal_period"
    QUARTER = "quarter"
    MONTH = "month"
    DATE = "date"
    STRING = "string"
    MIXED = "mixed"


class SemanticRole(str, Enum):
    METRIC = "metric"
    DIMENSION = "dimension"
    TIME_PERIOD = "time_period"
    IDENTIFIER = "identifier"
    CATEGORY = "category"
    BOOLEAN_FLAG = "boolean_flag"
    FREE_TEXT = "free_text"
    UNKNOWN = "unknown"


class TableOrientation(str, Enum):
    ORDINARY_RECORDS = "ordinary_records"
    WIDE_TIME_SERIES = "wide_time_series"
    TRANSPOSED = "transposed"
    KEY_VALUE = "key_value"
    MATRIX = "matrix"
    CONTINUATION = "continuation"
    SUMMARY = "summary"
    PRIMARILY_TEXTUAL = "primarily_textual"
    UNKNOWN = "unknown"


class ProfileQualityWarning(str, Enum):
    EMPTY_DATASET = "empty_dataset"
    HIGH_MISSINGNESS = "high_missingness"
    DUPLICATE_ROWS = "duplicate_rows"
    MIXED_COLUMN_TYPES = "mixed_column_types"
    DECLARED_TYPE_MISMATCH = "declared_type_mismatch"
    REPEATED_HEADER_ROWS = "repeated_header_rows"
    LOW_INFORMATION = "low_information"
    PRIMARILY_TEXTUAL = "primarily_textual"


class ValueFrequency(BaseModel):
    value: str
    count: int = Field(ge=1)
    percentage: float = Field(ge=0, le=100)

    model_config = ConfigDict(frozen=True, extra="forbid")


class NumericColumnStatistics(BaseModel):
    minimum: float
    maximum: float
    mean: float
    median: float
    standard_deviation: float = Field(ge=0)
    percentile_05: float
    percentile_25: float
    percentile_75: float
    percentile_95: float
    zero_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    potential_outlier_count: int = Field(ge=0)

    model_config = ConfigDict(frozen=True, extra="forbid")


class TimeColumnStatistics(BaseModel):
    detected_formats: tuple[str, ...] = ()
    minimum_period: str | None = None
    maximum_period: str | None = None
    missing_intervals: tuple[str, ...] = ()

    model_config = ConfigDict(frozen=True, extra="forbid")


class StringColumnStatistics(BaseModel):
    most_frequent_values: tuple[ValueFrequency, ...] = ()
    average_text_length: float = Field(ge=0)
    possible_identifier: bool
    low_cardinality: bool
    repeated_header_value_count: int = Field(ge=0)
    total_or_subtotal_value_count: int = Field(ge=0)
    footnote_like_value_count: int = Field(ge=0)

    model_config = ConfigDict(frozen=True, extra="forbid")


class ColumnProfile(BaseModel):
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    declared_type: str = Field(min_length=1)
    inferred_type: ProfiledDataType
    semantic_role: SemanticRole
    total_count: int = Field(ge=0)
    non_null_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    missing_percentage: float = Field(ge=0, le=100)
    unique_count: int = Field(ge=0)
    cardinality_ratio: float = Field(ge=0, le=1)
    example_values: tuple[str, ...] = Field(default=(), max_length=3)
    detected_unit: str | None = None
    type_confidence: float = Field(ge=0, le=1)
    parsing_warnings: tuple[str, ...] = ()
    numeric_statistics: NumericColumnStatistics | None = None
    time_statistics: TimeColumnStatistics | None = None
    string_statistics: StringColumnStatistics | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.non_null_count + self.missing_count != self.total_count:
            raise ValueError("non-null and missing counts must equal total_count")
        if self.unique_count > self.non_null_count:
            raise ValueError("unique_count cannot exceed non_null_count")
        return self


class DatasetProfile(BaseModel):
    dataset_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    profiler_version: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=1)
    is_empty: bool
    duplicate_row_count: int = Field(ge=0)
    repeated_header_row_count: int = Field(ge=0)
    total_or_subtotal_row_count: int = Field(ge=0)
    footnote_like_row_count: int = Field(ge=0)
    periods_in_headers: bool
    periods_in_rows: bool
    orientation: TableOrientation
    quality_score: float = Field(ge=0, le=1)
    quality_warnings: tuple[ProfileQualityWarning, ...] = ()
    suitable_for_analysis: bool
    columns: tuple[ColumnProfile, ...] = Field(min_length=1)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.column_count != len(self.columns):
            raise ValueError("column_count must match the number of column profiles")
        if self.is_empty != (self.row_count == 0):
            raise ValueError("is_empty must match row_count")
        return self


class ProfileFailureReason(str, Enum):
    NOT_AVAILABLE = "not_available"
    INVALID_TABLE = "invalid_table"
    SOURCE_VERSION_MISMATCH = "source_version_mismatch"
    PROFILING_FAILED = "profiling_failed"


class DatasetProfileFailure(BaseModel):
    dataset_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    reason: ProfileFailureReason
    message: str = Field(min_length=1)
    retryable: bool = False

    model_config = ConfigDict(frozen=True, extra="forbid")


class DatasetProfiles(BaseModel):
    """Checkpoint-safe profiling result for every hydrated dataset."""

    profiler_version: str = Field(min_length=1)
    status: Literal["complete", "partial", "empty", "failed"]
    profiles: tuple[DatasetProfile, ...] = ()
    failures: tuple[DatasetProfileFailure, ...] = ()
    requested_count: int = Field(ge=0)
    profiled_count: int = Field(ge=0)
    cache_hit_count: int = Field(ge=0)
    generated_count: int = Field(ge=0)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if self.profiled_count != len(self.profiles):
            raise ValueError("profiled_count must match profiles")
        if self.requested_count != len(self.profiles) + len(self.failures):
            raise ValueError("every requested dataset needs a profile or failure")
        if self.cache_hit_count + self.generated_count != self.profiled_count:
            raise ValueError("profile origin counts must match profiled_count")
        expected_status = (
            "empty"
            if self.requested_count == 0
            else "complete"
            if self.profiled_count == self.requested_count
            else "failed"
            if self.profiled_count == 0
            else "partial"
        )
        if self.status != expected_status:
            raise ValueError("status does not match profile counts")
        dataset_ids = [item.dataset_id for item in (*self.profiles, *self.failures)]
        if len(dataset_ids) != len(set(dataset_ids)):
            raise ValueError("dataset profile results must have unique dataset IDs")
        if any(
            profile.profiler_version != self.profiler_version
            for profile in self.profiles
        ):
            raise ValueError("profile versions must match the artifact version")
        return self


def profile_cache_key(
    *,
    dataset_id: str,
    source_version: str,
    profiler_version: str = DATASET_PROFILER_VERSION,
) -> str:
    payload = f"{dataset_id}\x1f{source_version}\x1f{profiler_version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
