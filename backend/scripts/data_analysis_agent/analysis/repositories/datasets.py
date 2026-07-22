from __future__ import annotations

from typing import Any, Protocol, Sequence

from db.mongodb import get_db


class DatasetRepositoryError(RuntimeError):
    """Raised when source datasets cannot be materialized."""


class DatasetRepository(Protocol):
    async def load_tables(
        self,
        *,
        user_id: str,
        document_ids: Sequence[str],
        table_ids: Sequence[str],
    ) -> tuple[dict[str, Any], ...]: ...


class MongoDatasetRepository:
    """Materialize full table rows in one tenant-scoped batch read."""

    async def load_tables(
        self,
        *,
        user_id: str,
        document_ids: Sequence[str],
        table_ids: Sequence[str],
    ) -> tuple[dict[str, Any], ...]:
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
            raise DatasetRepositoryError("source datasets could not be loaded") from exc
        return tuple(dict(table) for table in tables)
