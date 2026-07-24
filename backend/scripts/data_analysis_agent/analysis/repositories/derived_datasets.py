from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from db.mongodb import get_db

from ..models.completion import DerivedDatasetReference, EvidenceFact


class DerivedDatasetRepositoryError(RuntimeError):
    """Raised when validated text-derived data cannot be persisted."""


@dataclass(frozen=True, slots=True)
class DerivedDatasetWrite:
    reference: DerivedDatasetReference
    rows: tuple[dict[str, Any], ...]
    facts: tuple[EvidenceFact, ...]


class DerivedDatasetRepository(Protocol):
    async def save(
        self,
        *,
        user_id: str,
        value: DerivedDatasetWrite,
    ) -> DerivedDatasetReference: ...


class MongoDerivedDatasetRepository:
    collection_name = "derived_datasets"

    async def save(
        self,
        *,
        user_id: str,
        value: DerivedDatasetWrite,
    ) -> DerivedDatasetReference:
        reference = value.reference
        now = datetime.now(timezone.utc)
        document = {
            "user_id": user_id,
            "derived_dataset_id": reference.derived_dataset_id,
            "document_id": reference.document_id,
            "reference": reference.model_dump(mode="python"),
            "rows": list(value.rows),
            "fact_provenance": [
                fact.model_dump(mode="python") for fact in value.facts
            ],
            "origin": "llm_text_extraction",
            "content_type": "text_derived_dataset",
            "updated_at": now,
        }
        try:
            await get_db()[self.collection_name].update_one(
                {
                    "user_id": user_id,
                    "derived_dataset_id": reference.derived_dataset_id,
                },
                {
                    "$set": document,
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
        except Exception as exc:
            raise DerivedDatasetRepositoryError(
                "derived dataset could not be written"
            ) from exc
        return reference
