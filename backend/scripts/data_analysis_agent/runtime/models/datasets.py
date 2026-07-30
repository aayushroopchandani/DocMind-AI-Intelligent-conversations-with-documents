from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)

from .artifacts import BlobReference


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_A1_RANGE_RE = re.compile(
    r"^(?:'(?P<quoted>(?:[^']|'')+)'|(?P<plain>[^'!]+))?!?"
    r"(?P<start_col>[A-Z]{1,3})(?P<start_row>[1-9][0-9]*)"
    r":(?P<end_col>[A-Z]{1,3})(?P<end_row>[1-9][0-9]*)$"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DatasetSourceType(str, Enum):
    PDF_TABLE = "pdf_table"
    SPREADSHEET_RANGE = "spreadsheet_range"
    UPLOADED_CSV = "uploaded_csv"
    UPLOADED_XLSX = "uploaded_xlsx"
    DERIVED_DATASET = "derived_dataset"
    GENERATED_DATASET = "generated_dataset"


class DatasetColumnType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"


class DatasetColumn(BaseModel):
    """Stable source column; keys are identities and labels are display text."""

    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=240)
    type: DatasetColumnType
    unit: str | None = Field(default=None, max_length=100)
    source_index: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class PdfTableLocator(BaseModel):
    source_type: Literal[DatasetSourceType.PDF_TABLE] = (
        DatasetSourceType.PDF_TABLE
    )
    document_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    table_id: str = Field(min_length=1, max_length=240)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    extraction_method: Literal["pymupdf", "docling"]

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_pages(self) -> Self:
        if self.page_end < self.page_start:
            raise ValueError("page_end cannot precede page_start")
        return self


class SpreadsheetRangeLocator(BaseModel):
    source_type: Literal[DatasetSourceType.SPREADSHEET_RANGE] = (
        DatasetSourceType.SPREADSHEET_RANGE
    )
    artifact_id: str = Field(min_length=1, max_length=200)
    artifact_version_id: str = Field(min_length=1, max_length=200)
    workbook_id: str = Field(min_length=1, max_length=200)
    workbook_revision: int = Field(ge=0)
    worksheet_id: str = Field(min_length=1, max_length=200)
    worksheet_name: str = Field(min_length=1, max_length=255)
    range_a1: str = Field(min_length=5, max_length=100)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @field_validator("range_a1", mode="before")
    @classmethod
    def normalize_range(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not _A1_RANGE_RE.fullmatch(normalized.upper()):
            raise ValueError("range_a1 must be a bounded A1 range")
        return normalized


class UploadedFileLocator(BaseModel):
    source_type: Literal[
        DatasetSourceType.UPLOADED_CSV,
        DatasetSourceType.UPLOADED_XLSX,
    ]
    artifact_id: str = Field(min_length=1, max_length=200)
    artifact_version_id: str = Field(min_length=1, max_length=200)
    sheet_name: str | None = Field(default=None, max_length=255)
    range_a1: str | None = Field(default=None, max_length=100)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class DerivedDatasetLocator(BaseModel):
    source_type: Literal[
        DatasetSourceType.DERIVED_DATASET,
        DatasetSourceType.GENERATED_DATASET,
    ]
    parent_dataset_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    producing_run_id: str = Field(min_length=36, max_length=36)

    model_config = ConfigDict(extra="forbid", frozen=True)


DatasetLocator = Annotated[
    PdfTableLocator
    | SpreadsheetRangeLocator
    | UploadedFileLocator
    | DerivedDatasetLocator,
    Field(discriminator="source_type"),
]
_LOCATOR_ADAPTER = TypeAdapter(DatasetLocator)


class MongoDatasetStorage(BaseModel):
    provider: Literal["mongodb"] = "mongodb"
    collection: Literal["structured_tables", "normalized_datasets"]
    record_id: str = Field(min_length=1, max_length=240)

    model_config = ConfigDict(extra="forbid", frozen=True)


class BlobDatasetStorage(BaseModel):
    provider: Literal["blob"] = "blob"
    artifact_version_id: str = Field(min_length=1, max_length=200)
    blob: BlobReference
    encoding: Literal["tabular_json_gzip", "tabular_json", "csv", "xlsx"]

    model_config = ConfigDict(extra="forbid", frozen=True)


DatasetStorageReference = Annotated[
    MongoDatasetStorage | BlobDatasetStorage,
    Field(discriminator="provider"),
]


class DatasetHandle(BaseModel):
    """Immutable, source-neutral identity used throughout the analysis graph."""

    schema_version: Literal[1] = 1
    dataset_id: str = Field(min_length=1, max_length=240)
    user_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    source_type: DatasetSourceType
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=240)
    columns: tuple[DatasetColumn, ...] = Field(min_length=1, max_length=500)
    row_count: int = Field(ge=0)
    locator: DatasetLocator
    storage: DatasetStorageReference
    created_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    @field_validator("source_version", "content_hash", mode="before")
    @classmethod
    def normalize_hash(cls, value: object) -> str:
        normalized = str(value or "").strip().casefold()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("dataset hashes must be lowercase SHA-256 digests")
        return normalized

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.locator.source_type != self.source_type:
            raise ValueError("locator source_type must match the dataset")
        keys = [column.key for column in self.columns]
        if len(keys) != len(set(keys)):
            raise ValueError("dataset column keys must be unique")
        if self.source_type == DatasetSourceType.PDF_TABLE:
            if not (
                isinstance(self.storage, MongoDatasetStorage)
                and self.storage.collection == "structured_tables"
            ):
                raise ValueError(
                    "PDF tables must reference MongoDB structured_tables"
                )
        elif self.source_type in {
            DatasetSourceType.SPREADSHEET_RANGE,
            DatasetSourceType.UPLOADED_CSV,
            DatasetSourceType.UPLOADED_XLSX,
        } and not isinstance(self.storage, BlobDatasetStorage):
            raise ValueError(
                "uploaded and spreadsheet datasets require immutable blob storage"
            )
        elif (
            isinstance(self.storage, MongoDatasetStorage)
            and self.storage.collection != "normalized_datasets"
        ):
            raise ValueError(
                "derived MongoDB datasets must reference normalized_datasets"
            )
        return self

    @property
    def source_container_id(self) -> str:
        locator = self.locator
        if isinstance(locator, PdfTableLocator):
            return locator.document_id
        if isinstance(locator, SpreadsheetRangeLocator):
            return locator.artifact_id
        if isinstance(locator, UploadedFileLocator):
            return locator.artifact_id
        return locator.producing_run_id


class TabularDataset(BaseModel):
    """Transient materialized rows consumed by profiling and normalization."""

    dataset_id: str = Field(min_length=1, max_length=240)
    user_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    source_type: DatasetSourceType
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=240)
    columns: tuple[DatasetColumn, ...] = Field(min_length=1, max_length=500)
    rows: tuple[dict[str, JsonValue], ...] = Field(default=(), max_length=1_000_000)
    locator: DatasetLocator

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @model_validator(mode="after")
    def validate_rows(self) -> Self:
        if self.locator.source_type != self.source_type:
            raise ValueError("locator source_type must match the dataset")
        keys = tuple(column.key for column in self.columns)
        if len(keys) != len(set(keys)):
            raise ValueError("dataset column keys must be unique")
        expected = set(keys)
        for row in self.rows:
            if set(row) != expected:
                raise ValueError("every tabular row must match the dataset schema")
        return self

    def handle(self, *, storage: DatasetStorageReference) -> DatasetHandle:
        return DatasetHandle(
            dataset_id=self.dataset_id,
            user_id=self.user_id,
            workspace_id=self.workspace_id,
            source_type=self.source_type,
            source_version=self.source_version,
            content_hash=self.source_version,
            title=self.title,
            columns=self.columns,
            row_count=len(self.rows),
            locator=self.locator,
            storage=storage,
        )

    @property
    def source_container_id(self) -> str:
        locator = self.locator
        if isinstance(locator, PdfTableLocator):
            return locator.document_id
        if isinstance(locator, SpreadsheetRangeLocator):
            return locator.artifact_id
        if isinstance(locator, UploadedFileLocator):
            return locator.artifact_id
        return locator.producing_run_id


class DatasetCatalogEntry(BaseModel):
    """Metadata registry record. Full rows remain in the referenced storage."""

    handle: DatasetHandle
    discovery_summary: str = Field(default="", max_length=1600)
    keywords: tuple[str, ...] = Field(default=(), max_length=40)
    registered_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @model_validator(mode="after")
    def validate_timestamps(self) -> Self:
        if self.updated_at < self.registered_at:
            raise ValueError("updated_at cannot precede registered_at")
        return self


def tabular_source_version(
    *,
    source_type: DatasetSourceType,
    title: str,
    columns: tuple[DatasetColumn, ...],
    rows: tuple[dict[str, JsonValue], ...],
    locator: DatasetLocator,
) -> str:
    """Return a reproducible content+provenance version without copying rows."""

    digest = hashlib.sha256()

    def update(label: str, value: object) -> None:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
        digest.update(label.encode("ascii"))
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)

    update(
        "metadata",
        {
            "source_type": source_type.value,
            "title": title,
            "locator": _LOCATOR_ADAPTER.dump_python(locator, mode="json"),
        },
    )
    for column in columns:
        update("column", column.model_dump(mode="json"))
    for row in rows:
        update("row", row)
    return digest.hexdigest()


__all__ = [
    "BlobDatasetStorage",
    "DatasetCatalogEntry",
    "DatasetColumn",
    "DatasetColumnType",
    "DatasetHandle",
    "DatasetLocator",
    "DatasetSourceType",
    "DatasetStorageReference",
    "DerivedDatasetLocator",
    "MongoDatasetStorage",
    "PdfTableLocator",
    "SpreadsheetRangeLocator",
    "TabularDataset",
    "UploadedFileLocator",
    "tabular_source_version",
]
