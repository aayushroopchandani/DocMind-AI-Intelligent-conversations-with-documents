from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol, Sequence

from pydantic import ValidationError

from db.mongodb import get_db

from ..models.datasets import DatasetCatalogEntry, DatasetHandle


class DatasetCatalogError(RuntimeError):
    """Dataset metadata could not be read or persisted."""


class DatasetCatalogConflictError(DatasetCatalogError):
    """A stable dataset identity was reused with different metadata."""


class DatasetCatalogRepository(Protocol):
    async def register(
        self,
        entry: DatasetCatalogEntry,
    ) -> DatasetCatalogEntry: ...

    async def get_owned(
        self,
        *,
        user_id: str,
        workspace_id: str,
        dataset_id: str,
        source_version: str,
    ) -> DatasetCatalogEntry | None: ...

    async def load_handles(
        self,
        *,
        user_id: str,
        workspace_id: str,
        versions: Sequence[tuple[str, str]],
    ) -> tuple[DatasetHandle, ...]: ...


class MongoDatasetCatalogRepository:
    collection_name = "dataset_catalog"

    async def register(
        self,
        entry: DatasetCatalogEntry,
    ) -> DatasetCatalogEntry:
        handle = entry.handle
        query = {
            "user_id": handle.user_id,
            "workspace_id": handle.workspace_id,
            "dataset_id": handle.dataset_id,
            "source_version": handle.source_version,
        }
        document = entry.model_dump(mode="python")
        document.update(
            {
                "user_id": handle.user_id,
                "workspace_id": handle.workspace_id,
                "dataset_id": handle.dataset_id,
                "source_version": handle.source_version,
                "source_type": handle.source_type.value,
                "artifact_id": getattr(handle.locator, "artifact_id", None),
                "artifact_version_id": getattr(
                    handle.locator,
                    "artifact_version_id",
                    None,
                ),
            }
        )
        try:
            collection = get_db()[self.collection_name]
            await collection.update_one(
                query,
                {"$setOnInsert": document},
                upsert=True,
            )
            stored = await collection.find_one(query, {"_id": 0})
        except Exception as exc:
            raise DatasetCatalogError(
                "dataset catalog entry could not be registered"
            ) from exc
        parsed = _parse_entry(stored)
        if not _same_immutable_handle(parsed.handle, handle):
            raise DatasetCatalogConflictError(
                "dataset identity already exists with different metadata"
            )
        return parsed

    async def get_owned(
        self,
        *,
        user_id: str,
        workspace_id: str,
        dataset_id: str,
        source_version: str,
    ) -> DatasetCatalogEntry | None:
        try:
            stored = await get_db()[self.collection_name].find_one(
                {
                    "user_id": user_id,
                    "workspace_id": workspace_id,
                    "dataset_id": dataset_id,
                    "source_version": source_version,
                },
                {"_id": 0},
            )
        except Exception as exc:
            raise DatasetCatalogError("dataset catalog entry could not be read") from exc
        return _parse_entry(stored) if stored is not None else None

    async def load_handles(
        self,
        *,
        user_id: str,
        workspace_id: str,
        versions: Sequence[tuple[str, str]],
    ) -> tuple[DatasetHandle, ...]:
        unique = tuple(dict.fromkeys(versions))
        if not unique:
            return ()
        clauses = [
            {"dataset_id": dataset_id, "source_version": source_version}
            for dataset_id, source_version in unique
        ]
        try:
            documents = await get_db()[self.collection_name].find(
                {
                    "user_id": user_id,
                    "workspace_id": workspace_id,
                    "$or": clauses,
                },
                {"_id": 0},
            ).to_list(length=len(unique))
        except Exception as exc:
            raise DatasetCatalogError("dataset handles could not be loaded") from exc
        by_identity = {
            (entry.handle.dataset_id, entry.handle.source_version): entry.handle
            for entry in (_parse_entry(item) for item in documents)
        }
        return tuple(
            by_identity[identity] for identity in unique if identity in by_identity
        )


def _parse_entry(document: object) -> DatasetCatalogEntry:
    if not isinstance(document, dict):
        raise DatasetCatalogError("dataset catalog returned an invalid record")
    # Projection-friendly denormalized fields are not part of the domain model.
    payload = _normalize_datetimes({
        key: value
        for key, value in document.items()
        if key in DatasetCatalogEntry.model_fields
    })
    try:
        return DatasetCatalogEntry.model_validate(payload)
    except ValidationError as exc:
        raise DatasetCatalogError(
            "dataset catalog returned an invalid record"
        ) from exc


def _normalize_datetimes(value: Any) -> Any:
    """Normalize BSON dates for clients not configured with ``tz_aware``."""

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, dict):
        return {key: _normalize_datetimes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_datetimes(item) for item in value]
    return value


def _same_immutable_handle(left: DatasetHandle, right: DatasetHandle) -> bool:
    """Compare identity metadata while ignoring persistence timestamp precision.

    BSON stores datetimes to milliseconds, whereas a newly built Pydantic model
    may contain microseconds. ``created_at`` is audit metadata, not part of the
    immutable dataset identity.
    """

    return left.model_dump(mode="json", exclude={"created_at"}) == right.model_dump(
        mode="json",
        exclude={"created_at"},
    )


__all__ = [
    "DatasetCatalogConflictError",
    "DatasetCatalogError",
    "DatasetCatalogRepository",
    "MongoDatasetCatalogRepository",
]
