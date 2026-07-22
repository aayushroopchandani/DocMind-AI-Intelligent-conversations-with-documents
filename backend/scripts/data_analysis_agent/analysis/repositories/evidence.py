from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from db.mongodb import get_db


class EvidenceRepositoryError(RuntimeError):
    """Raised when authoritative evidence cannot be read."""


@dataclass(frozen=True, slots=True)
class HydrationSourceBatch:
    """Transient source records; never stored in LangGraph state."""

    tables: tuple[dict[str, Any], ...]
    documents: tuple[dict[str, Any], ...]


class EvidenceRepository(Protocol):
    async def load_sources(
        self,
        *,
        user_id: str,
        document_ids: Sequence[str],
        table_ids: Sequence[str],
    ) -> HydrationSourceBatch: ...


class MongoEvidenceRepository:
    """Batch reader scoped by tenant and selected document identifiers."""

    async def load_sources(
        self,
        *,
        user_id: str,
        document_ids: Sequence[str],
        table_ids: Sequence[str],
    ) -> HydrationSourceBatch:
        unique_document_ids = tuple(dict.fromkeys(document_ids))
        unique_table_ids = tuple(dict.fromkeys(table_ids))
        if not user_id.strip() or not unique_document_ids:
            raise ValueError("user_id and document_ids are required for hydration")
        if not unique_table_ids:
            return HydrationSourceBatch(tables=(), documents=())

        try:
            db = get_db()
            table_cursor = db.structured_tables.find(
                {
                    "user_id": user_id,
                    "document_id": {"$in": list(unique_document_ids)},
                    "table_id": {"$in": list(unique_table_ids)},
                },
                {"_id": 0},
            )
            document_cursor = db.documents.find(
                {
                    "user_id": user_id,
                    "document_id": {"$in": list(unique_document_ids)},
                },
                {
                    "_id": 0,
                    "document_id": 1,
                    "filename": 1,
                    "pages": 1,
                    "ingestion_status": 1,
                    "table_ingestion_status": 1,
                },
            )
            tables, documents = await asyncio.gather(
                table_cursor.to_list(length=len(unique_table_ids)),
                document_cursor.to_list(length=len(unique_document_ids)),
            )
        except Exception as exc:
            raise EvidenceRepositoryError(
                "authoritative table evidence could not be loaded"
            ) from exc

        return HydrationSourceBatch(
            tables=tuple(dict(table) for table in tables),
            documents=tuple(dict(document) for document in documents),
        )
