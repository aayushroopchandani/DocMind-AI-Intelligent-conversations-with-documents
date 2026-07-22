from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    provider: Literal["mongodb"] = "mongodb"
    collection: Literal["structured_tables"] = "structured_tables"
    table_id: str = Field(min_length=1)

    model_config = ConfigDict(frozen=True, extra="forbid")


class HydratedDatasetReference(BaseModel):
    """Verified, checkpoint-safe handle to a full source table."""

    dataset_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    document_name: str = ""
    title: str = Field(min_length=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    extraction_method: Literal["pymupdf", "docling"]
    columns: tuple[DatasetColumn, ...] = Field(min_length=1)
    row_count: int = Field(ge=0)
    source_regions: tuple[SourceRegion, ...] = Field(min_length=1)
    access: DatasetAccessReference
    usable_for_analysis: bool
    retrieval_score: float | None = Field(default=None, ge=0)
    matched_queries: tuple[str, ...] = ()
    retrieval_modes: tuple[str, ...] = ()

    model_config = ConfigDict(frozen=True, extra="forbid")


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
