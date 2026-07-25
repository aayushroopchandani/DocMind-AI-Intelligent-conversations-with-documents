from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from pydantic import ValidationError

from db.mongodb import get_db

from ..models.completion import RepairRetrievalCacheEntry


class RepairRetrievalCacheError(RuntimeError):
    """Raised when a targeted-retrieval cache operation fails."""


class RepairRetrievalCache(Protocol):
    async def load(
        self,
        *,
        user_id: str,
        cache_key: str,
    ) -> RepairRetrievalCacheEntry | None: ...

    async def save(
        self,
        *,
        user_id: str,
        cache_key: str,
        entry: RepairRetrievalCacheEntry,
    ) -> None: ...


class MongoRepairRetrievalCache:
    collection_name = "analysis_repair_retrieval_cache"

    async def load(
        self,
        *,
        user_id: str,
        cache_key: str,
    ) -> RepairRetrievalCacheEntry | None:
        try:
            value = await get_db()[self.collection_name].find_one(
                {
                    "user_id": user_id,
                    "cache_key": cache_key,
                    "expires_at": {"$gt": datetime.now(timezone.utc)},
                },
                {"_id": 0, "entry": 1},
            )
        except Exception as exc:
            raise RepairRetrievalCacheError(
                "targeted-retrieval cache could not be read"
            ) from exc
        if not value:
            return None
        try:
            return RepairRetrievalCacheEntry.model_validate(value.get("entry"))
        except ValidationError:
            return None

    async def save(
        self,
        *,
        user_id: str,
        cache_key: str,
        entry: RepairRetrievalCacheEntry,
    ) -> None:
        now = datetime.now(timezone.utc)
        try:
            await get_db()[self.collection_name].update_one(
                {"user_id": user_id, "cache_key": cache_key},
                {
                    "$set": {
                        "entry": entry.model_dump(mode="python"),
                        "expires_at": entry.expires_at,
                        "updated_at": now,
                    },
                    "$setOnInsert": {
                        "user_id": user_id,
                        "cache_key": cache_key,
                        "created_at": now,
                    },
                },
                upsert=True,
            )
        except Exception as exc:
            raise RepairRetrievalCacheError(
                "targeted-retrieval cache could not be written"
            ) from exc
