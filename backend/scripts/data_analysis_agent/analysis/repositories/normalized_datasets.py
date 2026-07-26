from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from pydantic import ValidationError

from db.mongodb import get_db

from ..models import NormalizedDatasetReference


class NormalizedDatasetRepositoryError(RuntimeError):
    """Raised when prepared dataset metadata or rows cannot be persisted."""


@dataclass(frozen=True, slots=True)
class NormalizedDatasetWrite:
    reference: NormalizedDatasetReference
    rows: tuple[dict[str, Any], ...] = ()
    lineage: tuple[Mapping[str, Any], ...] = ()
    excluded_rows: tuple[Mapping[str, Any], ...] = ()
    footnotes: tuple[Mapping[str, Any], ...] = ()


class NormalizedDatasetRepository(Protocol):
    async def load_many(
        self,
        *,
        user_id: str,
        cache_keys: Sequence[str],
    ) -> dict[str, NormalizedDatasetReference]: ...

    async def save(
        self,
        *,
        user_id: str,
        value: NormalizedDatasetWrite,
    ) -> NormalizedDatasetReference: ...


class MongoNormalizedDatasetRepository:
    collection_name = "normalized_datasets"

    async def load_many(
        self,
        *,
        user_id: str,
        cache_keys: Sequence[str],
    ) -> dict[str, NormalizedDatasetReference]:
        unique_keys = tuple(dict.fromkeys(cache_keys))
        if not unique_keys:
            return {}
        try:
            cursor = get_db()[self.collection_name].find(
                {
                    "user_id": user_id,
                    "cache_key": {"$in": list(unique_keys)},
                },
                {"_id": 0, "cache_key": 1, "reference": 1},
            )
            documents = await cursor.to_list(length=len(unique_keys))
        except Exception as exc:
            raise NormalizedDatasetRepositoryError(
                "normalized dataset cache could not be read"
            ) from exc
        output: dict[str, NormalizedDatasetReference] = {}
        for document in documents:
            try:
                reference = NormalizedDatasetReference.model_validate(
                    document.get("reference")
                )
            except ValidationError:
                continue
            cache_key = str(document.get("cache_key") or "")
            if cache_key == reference.cache_key:
                output[cache_key] = reference
        return output

    async def save(
        self,
        *,
        user_id: str,
        value: NormalizedDatasetWrite,
    ) -> NormalizedDatasetReference:
        reference = value.reference
        now = datetime.now(timezone.utc)
        document: dict[str, Any] = {
            "user_id": user_id,
            "cache_key": reference.cache_key,
            "normalized_dataset_id": reference.normalized_dataset_id,
            "source_dataset_ids": list(reference.source_dataset_ids),
            "source_versions": list(reference.source_versions),
            "document_id": reference.document_id,
            "materialization": reference.materialization.value,
            "reference": reference.model_dump(mode="python"),
            "lineage": [dict(item) for item in value.lineage],
            "excluded_rows": [dict(item) for item in value.excluded_rows],
            "footnotes": [dict(item) for item in value.footnotes],
            "updated_at": now,
        }
        if reference.materialization.value == "materialized_dataset":
            document["rows"] = list(value.rows)
        try:
            await get_db()[self.collection_name].update_one(
                {"user_id": user_id, "cache_key": reference.cache_key},
                {
                    "$set": document,
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
        except Exception as exc:
            raise NormalizedDatasetRepositoryError(
                "normalized dataset could not be written"
            ) from exc
        return reference
