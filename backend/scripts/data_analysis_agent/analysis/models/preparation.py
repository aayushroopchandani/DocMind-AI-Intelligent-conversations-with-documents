from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .profile import SemanticRole

DATASET_NORMALIZER_VERSION = "1.2.0"


def preparation_utc_now() -> datetime:
    return datetime.now(timezone.utc)


class NormalizationStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"


class MaterializationType(str, Enum):
    SOURCE_PASSTHROUGH = "source_passthrough"
    MATERIALIZED_DATASET = "materialized_dataset"


class NormalizedDataType(str, Enum):
    STRING = "string"
    DECIMAL = "decimal"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    PERIOD = "period"
    UNKNOWN = "unknown"


class TransformationOperation(str, Enum):
    NORMALIZE_TEXT = "normalize_text"
    NORMALIZE_MISSING = "normalize_missing"
    PARSE_NUMERIC = "parse_numeric"
    PARSE_PERIOD = "parse_period"
    REMOVE_EXACT_DUPLICATES = "remove_exact_duplicates"
    REMOVE_REPEATED_HEADERS = "remove_repeated_headers"
    CLASSIFY_TOTAL_ROWS = "classify_total_rows"
    SEPARATE_FOOTNOTES = "separate_footnotes"
    RESHAPE_WIDE_TO_LONG = "reshape_wide_to_long"
    RESHAPE_MATRIX_TO_LONG = "reshape_matrix_to_long"
    RESHAPE_TRANSPOSED_TO_LONG = "reshape_transposed_to_long"


class PreparationFailureReason(str, Enum):
    NOT_AVAILABLE = "not_available"
    SOURCE_VERSION_MISMATCH = "source_version_mismatch"
    INVALID_SOURCE = "invalid_source"
    TRANSFORMATION_FAILED = "transformation_failed"
    VALIDATION_FAILED = "validation_failed"
    PERSISTENCE_FAILED = "persistence_failed"


class NormalizedColumn(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=160)
    data_type: NormalizedDataType
    unit: str | None = Field(default=None, max_length=100)
    semantic_role: SemanticRole = SemanticRole.UNKNOWN
    source_column_keys: tuple[str, ...] = Field(default=(), max_length=64)

    model_config = ConfigDict(frozen=True, extra="forbid")


class TransformationSummary(BaseModel):
    transformation_id: str = Field(pattern=r"^transform_[a-f0-9]{16}$")
    operation: TransformationOperation
    input_columns: tuple[str, ...] = Field(default=(), max_length=64)
    reason: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0, le=1)
    reversible: bool
    affected_row_count: int = Field(default=0, ge=0)

    model_config = ConfigDict(frozen=True, extra="forbid")


class PreparedDatasetAccessReference(BaseModel):
    provider: Literal["mongodb", "blob"] = "mongodb"
    collection: Literal[
        "structured_tables",
        "normalized_datasets",
        "dataset_catalog",
    ]
    record_id: str = Field(min_length=1)
    artifact_version_id: str | None = Field(default=None, min_length=1)
    blob: dict[str, Any] | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_locator(self) -> Self:
        if self.provider == "mongodb":
            if self.collection == "dataset_catalog" or self.blob is not None:
                raise ValueError("MongoDB prepared access has an invalid locator")
        elif (
            self.collection != "dataset_catalog"
            or not self.artifact_version_id
            or not self.blob
        ):
            raise ValueError(
                "blob prepared access requires dataset catalog and artifact version"
            )
        return self


class NormalizedDatasetReference(BaseModel):
    """Checkpoint-safe handle to a source or materialized analysis dataset."""

    normalized_dataset_id: str = Field(pattern=r"^normalized_[a-f0-9]{24}$")
    normalizer_version: str = DATASET_NORMALIZER_VERSION
    cache_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    recipe_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    materialization: MaterializationType
    source_dataset_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    source_versions: tuple[str, ...] = Field(min_length=1, max_length=8)
    source_table_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    source_type: Literal[
        "pdf_table",
        "spreadsheet_range",
        "uploaded_csv",
        "uploaded_xlsx",
        "derived_dataset",
        "generated_dataset",
    ] = "pdf_table"
    document_id: str = Field(min_length=1)
    source_page_start: int | None = Field(default=None, ge=1)
    source_page_end: int | None = Field(default=None, ge=1)
    artifact_id: str | None = Field(default=None, min_length=1)
    artifact_version_id: str | None = Field(default=None, min_length=1)
    worksheet_id: str | None = Field(default=None, min_length=1)
    range_a1: str | None = Field(default=None, min_length=1)
    snapshot_hash: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    title: str = Field(min_length=1, max_length=240)
    requirement_ids: tuple[str, ...] = Field(min_length=1, max_length=48)
    # Keep this aligned with the source-neutral DatasetHandle contract and the
    # Phase 8 workbook adapter. Rejecting columns 129–500 only after profiling
    # wastes the expensive part of the pipeline and makes an accepted workbook
    # fail nondeterministically at normalization.
    columns: tuple[NormalizedColumn, ...] = Field(min_length=1, max_length=500)
    input_column_count: int = Field(ge=1)
    output_column_count: int = Field(ge=1)
    input_row_count: int = Field(ge=0)
    retained_source_row_count: int = Field(ge=0)
    output_row_count: int = Field(ge=0)
    duplicate_row_count: int = Field(default=0, ge=0)
    repeated_header_row_count: int = Field(default=0, ge=0)
    footnote_row_count: int = Field(default=0, ge=0)
    total_or_subtotal_row_count: int = Field(default=0, ge=0)
    numeric_parse_failure_count: int = Field(default=0, ge=0)
    period_parse_failure_count: int = Field(default=0, ge=0)
    quality_score_before: float = Field(ge=0, le=1)
    quality_score_after: float = Field(ge=0, le=1)
    transformations: tuple[TransformationSummary, ...] = Field(
        default=(),
        max_length=24,
    )
    validation_checks: tuple[str, ...] = Field(default=(), max_length=24)
    access: PreparedDatasetAccessReference
    cache_hit: bool = False

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        source_count = len(self.source_dataset_ids)
        if not (
            source_count
            == len(self.source_versions)
            == len(self.source_table_ids)
        ):
            raise ValueError("source dataset identities must have matching lengths")
        if self.materialization == MaterializationType.SOURCE_PASSTHROUGH:
            expected_collection = (
                "structured_tables"
                if self.source_type == "pdf_table"
                else "dataset_catalog"
            )
            if self.access.collection != expected_collection:
                raise ValueError(
                    "passthrough datasets must reference their immutable source"
                )
            if self.transformations:
                raise ValueError("passthrough datasets cannot report transformations")
            if self.output_row_count != self.input_row_count:
                raise ValueError("passthrough datasets must preserve row count")
        elif self.access.collection != "normalized_datasets":
            raise ValueError("materialized datasets must use normalized storage")
        if self.retained_source_row_count > self.input_row_count:
            raise ValueError("retained source rows cannot exceed input rows")
        if (self.source_page_start is None) != (self.source_page_end is None):
            raise ValueError("source page bounds must both be present or absent")
        if (
            self.source_page_start is not None
            and self.source_page_end is not None
            and self.source_page_end < self.source_page_start
        ):
            raise ValueError("source page range is invalid")
        if self.source_type == "pdf_table" and self.source_page_start is None:
            raise ValueError("PDF normalized datasets require source pages")
        if self.source_type != "pdf_table" and self.source_page_start is not None:
            raise ValueError("non-PDF normalized datasets cannot contain pages")
        if self.output_column_count != len(self.columns):
            raise ValueError("output-column count must match the schema")
        if self.numeric_parse_failure_count or self.period_parse_failure_count:
            raise ValueError("validated datasets cannot contain parsing failures")
        return self


class DatasetPreparationFailure(BaseModel):
    dataset_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    requirement_ids: tuple[str, ...] = Field(default=(), max_length=48)
    reason: PreparationFailureReason
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False

    model_config = ConfigDict(frozen=True, extra="forbid")


class NormalizationResult(BaseModel):
    """Minimal Phase 7 state artifact; source and output rows stay external."""

    normalizer_version: str = DATASET_NORMALIZER_VERSION
    run_id: str = Field(min_length=1)
    input_evidence_signature: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: NormalizationStatus
    datasets: tuple[NormalizedDatasetReference, ...] = Field(
        default=(),
        max_length=30,
    )
    selected_fact_ids: tuple[str, ...] = Field(default=(), max_length=30)
    selected_derived_dataset_ids: tuple[str, ...] = Field(
        default=(),
        max_length=10,
    )
    non_tabular_requirement_ids: tuple[str, ...] = Field(default=(), max_length=48)
    rejected_dataset_ids: tuple[str, ...] = Field(default=(), max_length=48)
    failures: tuple[DatasetPreparationFailure, ...] = Field(
        default=(),
        max_length=30,
    )
    selected_dataset_count: int = Field(ge=0)
    prepared_dataset_count: int = Field(ge=0)
    cache_hit_count: int = Field(ge=0)
    passthrough_count: int = Field(ge=0)
    materialized_count: int = Field(ge=0)
    total_input_rows: int = Field(ge=0)
    total_output_rows: int = Field(ge=0)
    can_analyze: bool
    created_at: datetime = Field(default_factory=preparation_utc_now)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.selected_dataset_count != len(self.datasets) + len(self.failures):
            raise ValueError("every selected dataset needs a result or failure")
        if self.prepared_dataset_count != len(self.datasets):
            raise ValueError("prepared count must match dataset references")
        if self.cache_hit_count != sum(item.cache_hit for item in self.datasets):
            raise ValueError("cache-hit count must match dataset references")
        if self.passthrough_count != sum(
            item.materialization == MaterializationType.SOURCE_PASSTHROUGH
            for item in self.datasets
        ):
            raise ValueError("passthrough count must match dataset references")
        if self.materialized_count != sum(
            item.materialization == MaterializationType.MATERIALIZED_DATASET
            for item in self.datasets
        ):
            raise ValueError("materialized count must match dataset references")
        if self.total_input_rows != sum(
            item.input_row_count for item in self.datasets
        ):
            raise ValueError("input-row total must match dataset references")
        if self.total_output_rows != sum(
            item.output_row_count for item in self.datasets
        ):
            raise ValueError("output-row total must match dataset references")
        expected_status = (
            NormalizationStatus.READY
            if not self.failures
            else NormalizationStatus.FAILED
            if not self.datasets
            else NormalizationStatus.PARTIAL
        )
        if self.status != expected_status:
            raise ValueError("normalization status does not match results")
        if self.can_analyze != (self.status == NormalizationStatus.READY):
            raise ValueError("analysis can continue only after complete preparation")
        for field_values in (
            self.selected_fact_ids,
            self.selected_derived_dataset_ids,
            self.non_tabular_requirement_ids,
            self.rejected_dataset_ids,
        ):
            if len(field_values) != len(set(field_values)):
                raise ValueError("normalization result references must be unique")
        return self
