from __future__ import annotations

import gzip
import io
import json
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from config.settings import settings
from db.models.structured_table import StructuredTable
from db.mongodb import get_db
from scripts.data_analysis_agent.runtime.models import (
    BlobReference,
    DatasetColumn,
    DatasetSourceType,
    PdfTableLocator,
    SpreadsheetRangeLocator,
    TabularDataset,
)
from scripts.data_analysis_agent.runtime.storage import (
    ArtifactBlobStore,
    BlobStoreError,
    CloudinaryArtifactBlobStore,
    CloudinaryBlobStoreConfig,
)

from ..models.evidence import HydratedDatasetReference


class DatasetRepositoryError(RuntimeError):
    """Raised when source datasets cannot be materialized."""


class DatasetRepository(Protocol):
    async def load_datasets(
        self,
        *,
        user_id: str,
        datasets: Sequence[HydratedDatasetReference],
    ) -> tuple[TabularDataset, ...]: ...


def structured_table_to_tabular(
    *,
    table: StructuredTable,
    reference: HydratedDatasetReference,
) -> TabularDataset:
    """Adapt the legacy PDF source record at the repository boundary."""

    # Import lazily to keep the low-level repository free of the analysis
    # services package's re-export cycle.
    from ..services.versioning import source_version

    return TabularDataset(
        dataset_id=reference.dataset_id,
        user_id=table.user_id,
        workspace_id=reference.workspace_id or table.chat_id or table.document_id,
        source_type=DatasetSourceType.PDF_TABLE,
        source_version=source_version(table),
        title=table.title,
        columns=tuple(
            DatasetColumn(
                key=column.key,
                label=column.label,
                type=column.type,
                unit=column.unit,
                source_index=index,
            )
            for index, column in enumerate(table.columns)
        ),
        rows=tuple(dict(row) for row in table.rows),
        locator=PdfTableLocator(
            document_id=table.document_id,
            table_id=table.table_id,
            page_start=table.page_start,
            page_end=table.page_end,
            extraction_method=table.extraction_method,
        ),
    )


class MongoDatasetRepository:
    """Materialize PDF rows from MongoDB and artifact rows from blob storage."""

    def __init__(self, *, blob_store: ArtifactBlobStore | None = None) -> None:
        self._blob_store = blob_store

    def _configured_blob_store(self) -> ArtifactBlobStore:
        if self._blob_store is not None:
            return self._blob_store
        if not settings.cloudinary_is_configured:
            raise DatasetRepositoryError(
                "artifact storage is not configured for spreadsheet datasets"
            )
        self._blob_store = CloudinaryArtifactBlobStore(
            CloudinaryBlobStoreConfig(
                cloud_name=settings.cloudinary_cloud_name,
                api_key=settings.cloudinary_api_key,
                api_secret=settings.cloudinary_api_secret,
                max_download_bytes=settings.analysis_max_artifact_bytes,
            )
        )
        return self._blob_store

    async def load_datasets(
        self,
        *,
        user_id: str,
        datasets: Sequence[HydratedDatasetReference],
    ) -> tuple[TabularDataset, ...]:
        if not user_id.strip():
            raise ValueError("user_id is required")
        if not datasets:
            return ()

        pdf_references = tuple(
            item for item in datasets if item.access.provider == "mongodb"
        )
        blob_references = tuple(
            item for item in datasets if item.access.provider == "blob"
        )
        materialized: dict[str, TabularDataset] = {}

        if pdf_references:
            raw_tables = await self.load_tables(
                user_id=user_id,
                document_ids=tuple(item.document_id for item in pdf_references),
                table_ids=tuple(item.table_id for item in pdf_references),
            )
            references_by_table = {
                item.table_id: item for item in pdf_references
            }
            for raw in raw_tables:
                try:
                    table = StructuredTable.model_validate(raw)
                    reference = references_by_table[table.table_id]
                    materialized[reference.dataset_id] = structured_table_to_tabular(
                        table=table,
                        reference=reference,
                    )
                except (KeyError, TypeError, ValueError):
                    continue

        if blob_references:
            store = self._configured_blob_store()
            for reference in blob_references:
                blob_payload = reference.access.blob
                if blob_payload is None:
                    continue
                try:
                    blob = BlobReference.model_validate(blob_payload)
                    content = await store.download(
                        blob,
                        max_bytes=settings.analysis_max_artifact_bytes,
                    )
                    dataset = self._decode_tabular_blob(
                        content,
                        encoding=_blob_encoding(blob.filename),
                    )
                except (BlobStoreError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise DatasetRepositoryError(
                        "artifact dataset rows could not be loaded"
                    ) from exc
                if (
                    dataset.user_id != user_id
                    or dataset.workspace_id != reference.workspace_id
                    or dataset.dataset_id != reference.dataset_id
                    or dataset.source_version != reference.source_version
                    or dataset.source_type.value != reference.source_type
                ):
                    raise DatasetRepositoryError(
                        "artifact dataset identity failed verification"
                    )
                materialized[reference.dataset_id] = dataset

        return tuple(
            materialized[item.dataset_id]
            for item in datasets
            if item.dataset_id in materialized
        )

    @staticmethod
    def _decode_tabular_blob(content: bytes, *, encoding: str) -> TabularDataset:
        payload = (
            _bounded_gzip_decode(
                content,
                max_bytes=settings.analysis_max_xlsx_uncompressed_bytes,
            )
            if encoding == "tabular_json_gzip"
            else content
        )
        return TabularDataset.model_validate_json(payload)

    async def load_tables(
        self,
        *,
        user_id: str,
        document_ids: Sequence[str],
        table_ids: Sequence[str],
    ) -> tuple[dict[str, Any], ...]:
        """Legacy batch reader retained for completion and compatibility tests."""

        unique_document_ids = tuple(dict.fromkeys(document_ids))
        unique_table_ids = tuple(dict.fromkeys(table_ids))
        if not user_id.strip() or not unique_document_ids:
            raise ValueError("user_id and document_ids are required")
        if not unique_table_ids:
            return ()
        try:
            cursor = get_db().structured_tables.find(
                {
                    "user_id": user_id,
                    "document_id": {"$in": list(unique_document_ids)},
                    "table_id": {"$in": list(unique_table_ids)},
                },
                {"_id": 0},
            )
            tables = await cursor.to_list(length=len(unique_table_ids))
        except Exception as exc:
            raise DatasetRepositoryError(
                "source datasets could not be loaded"
            ) from exc
        return tuple(dict(table) for table in tables)


async def load_materialized_datasets(
    repository: object,
    *,
    user_id: str,
    document_ids: Sequence[str],
    datasets: Sequence[HydratedDatasetReference],
) -> tuple[TabularDataset, ...]:
    """Compatibility shim for injected Phase 4-7 repositories.

    Production repositories implement ``load_datasets``. Existing focused
    tests and third-party adapters that still expose ``load_tables`` are
    adapted here without letting the legacy interface spread further.
    """

    batch = await load_materialized_dataset_batch(
        repository,
        user_id=user_id,
        document_ids=document_ids,
        datasets=datasets,
    )
    return batch.datasets


@dataclass(frozen=True, slots=True)
class MaterializedDatasetBatch:
    datasets: tuple[TabularDataset, ...]
    invalid_dataset_ids: frozenset[str] = frozenset()


async def load_materialized_dataset_batch(
    repository: object,
    *,
    user_id: str,
    document_ids: Sequence[str],
    datasets: Sequence[HydratedDatasetReference],
) -> MaterializedDatasetBatch:
    """Load datasets while preserving per-record structural failures."""

    loader = getattr(repository, "load_datasets", None)
    if callable(loader):
        return MaterializedDatasetBatch(
            datasets=await loader(user_id=user_id, datasets=datasets)
        )

    legacy_loader = getattr(repository, "load_tables", None)
    if not callable(legacy_loader):
        raise DatasetRepositoryError("dataset repository has no materializer")
    raw_tables = await legacy_loader(
        user_id=user_id,
        document_ids=document_ids,
        table_ids=tuple(item.table_id for item in datasets),
    )
    references = {item.table_id: item for item in datasets}
    output: list[TabularDataset] = []
    invalid: set[str] = set()
    for raw in raw_tables:
        table_id = str(raw.get("table_id") or "") if isinstance(raw, dict) else ""
        try:
            table = StructuredTable.model_validate(raw)
            reference = references[table.table_id]
            output.append(
                structured_table_to_tabular(table=table, reference=reference)
            )
        except (KeyError, TypeError, ValueError):
            if table_id in references:
                invalid.add(references[table_id].dataset_id)
            continue
    return MaterializedDatasetBatch(
        datasets=tuple(output),
        invalid_dataset_ids=frozenset(invalid),
    )


def _blob_encoding(filename: str) -> str:
    lowered = filename.casefold()
    if lowered.endswith((".json.gz", ".json.gzip")):
        return "tabular_json_gzip"
    if lowered.endswith(".json"):
        return "tabular_json"
    raise DatasetRepositoryError("dataset blob has an unsupported encoding")


def _bounded_gzip_decode(content: bytes, *, max_bytes: int) -> bytes:
    output = bytearray()
    with gzip.GzipFile(fileobj=io.BytesIO(content), mode="rb") as stream:
        while chunk := stream.read(64 * 1024):
            output.extend(chunk)
            if len(output) > max_bytes:
                raise DatasetRepositoryError(
                    "compressed dataset expands beyond the allowed size"
                )
    return bytes(output)


__all__ = [
    "DatasetRepository",
    "DatasetRepositoryError",
    "MongoDatasetRepository",
    "MaterializedDatasetBatch",
    "load_materialized_dataset_batch",
    "load_materialized_datasets",
    "structured_table_to_tabular",
]
