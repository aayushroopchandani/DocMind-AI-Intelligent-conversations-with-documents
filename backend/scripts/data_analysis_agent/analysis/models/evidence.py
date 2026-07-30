from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DatasetColumn(BaseModel):
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    type: Literal["string", "number", "boolean", "date"]
    unit: str | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceRegion(BaseModel):
    page: int = Field(ge=1)
    bounding_box: tuple[float, float, float, float]

    model_config = ConfigDict(frozen=True, extra="forbid")


class DatasetAccessReference(BaseModel):
    """Provider-neutral locator for materializing a hydrated dataset.

    PDF tables continue to use ``mongodb/structured_tables``. Spreadsheet
    ranges and uploaded files use an immutable blob plus a dataset-catalog
    record. Provider details remain an infrastructure concern and never leak
    into analysis plans.
    """

    provider: Literal["mongodb", "blob"] = "mongodb"
    collection: Literal["structured_tables", "dataset_catalog"] | None = (
        "structured_tables"
    )
    record_id: str | None = Field(default=None, min_length=1)
    table_id: str | None = Field(default=None, min_length=1)
    artifact_version_id: str | None = Field(default=None, min_length=1)
    blob: dict[str, Any] | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_provider_locator(self) -> Self:
        if self.provider == "mongodb":
            if self.collection != "structured_tables" or not self.table_id:
                raise ValueError(
                    "MongoDB dataset access requires a structured-table ID"
                )
            if self.blob is not None:
                raise ValueError("MongoDB dataset access cannot contain a blob")
        else:
            if self.collection != "dataset_catalog":
                raise ValueError("blob dataset access requires dataset_catalog")
            if not self.record_id or not self.artifact_version_id or not self.blob:
                raise ValueError(
                    "blob dataset access requires catalog, artifact-version, "
                    "and blob references"
                )
        return self


class HydratedDatasetReference(BaseModel):
    """Verified, checkpoint-safe handle to a full source table."""

    dataset_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    source_type: Literal[
        "pdf_table",
        "spreadsheet_range",
        "uploaded_csv",
        "uploaded_xlsx",
        "derived_dataset",
        "generated_dataset",
    ] = "pdf_table"
    workspace_id: str | None = Field(default=None, min_length=1)
    table_id: str = Field(min_length=1)
    # ``document_id`` is retained as the source-container identifier for
    # compatibility with Phase 4-7 assessment artifacts. For non-PDF sources
    # it is the workbook/file artifact ID, never a fabricated PDF hash.
    document_id: str = Field(min_length=1)
    document_name: str = ""
    title: str = Field(min_length=1)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    extraction_method: Literal[
        "pymupdf",
        "docling",
        "spreadsheet",
        "csv",
        "xlsx",
        "generated",
    ]
    columns: tuple[DatasetColumn, ...] = Field(min_length=1)
    row_count: int = Field(ge=0)
    source_regions: tuple[SourceRegion, ...] = ()
    artifact_id: str | None = Field(default=None, min_length=1)
    artifact_version_id: str | None = Field(default=None, min_length=1)
    worksheet_id: str | None = Field(default=None, min_length=1)
    worksheet_name: str | None = Field(default=None, min_length=1)
    range_a1: str | None = Field(default=None, min_length=1)
    workbook_revision: int | None = Field(default=None, ge=0)
    snapshot_hash: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    access: DatasetAccessReference
    usable_for_analysis: bool
    retrieval_score: float | None = Field(default=None, ge=0)
    matched_queries: tuple[str, ...] = ()
    retrieval_modes: tuple[str, ...] = ()

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_source_provenance(self) -> Self:
        if (self.page_start is None) != (self.page_end is None):
            raise ValueError("source page bounds must both be present or absent")
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("source page range is invalid")
        if self.source_type == "pdf_table":
            if self.page_start is None or not self.source_regions:
                raise ValueError("PDF datasets require page provenance")
            if self.access.provider != "mongodb":
                raise ValueError("PDF tables must use authoritative MongoDB rows")
        elif self.source_type == "spreadsheet_range":
            required = (
                self.workspace_id,
                self.artifact_id,
                self.artifact_version_id,
                self.worksheet_id,
                self.worksheet_name,
                self.range_a1,
                self.snapshot_hash,
            )
            if not all(required):
                raise ValueError(
                    "spreadsheet ranges require workbook, sheet, range, and "
                    "snapshot provenance"
                )
            if self.page_start is not None or self.source_regions:
                raise ValueError("spreadsheet ranges cannot contain PDF pages")
            if self.access.provider != "blob":
                raise ValueError("spreadsheet ranges require immutable blob access")
        return self


class UnresolvedTableReference(BaseModel):
    table_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    reason: Literal["not_available", "invalid"]

    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidencePackage(BaseModel):
    """Hydration outcome without copying source rows into graph state."""

    run_id: str = Field(min_length=1)
    status: Literal["complete", "partial", "empty", "failed"]
    datasets: tuple[HydratedDatasetReference, ...] = ()
    unresolved_tables: tuple[UnresolvedTableReference, ...] = ()
    retrieved_table_count: int = Field(ge=0)
    hydrated_table_count: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(frozen=True, extra="forbid")
