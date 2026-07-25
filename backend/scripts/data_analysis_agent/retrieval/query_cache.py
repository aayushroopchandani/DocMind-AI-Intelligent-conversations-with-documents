from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol

from db.mongodb import get_db


class QueryGenerationCacheError(RuntimeError):
    """Raised when generated retrieval queries cannot be cached."""


class QueryGenerationCache(Protocol):
    async def load(
        self,
        *,
        user_id: str,
        cache_key: str,
    ) -> Mapping[str, Any] | None: ...

    async def save(
        self,
        *,
        user_id: str,
        cache_key: str,
        result: Mapping[str, Any],
    ) -> None: ...


def query_generation_cache_key(
    *,
    query: str,
    document_ids: tuple[str, ...],
    model: str,
    prompt_version: str,
    generation_version: str,
) -> str:
    """Return a tenant-independent content key for deterministic cache reuse."""

    payload = {
        "query": " ".join(query.casefold().split()),
        "document_ids": sorted(set(document_ids)),
        "model": model,
        "prompt_version": prompt_version,
        "generation_version": generation_version,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class MongoQueryGenerationCache:
    collection_name = "analysis_query_generation_cache"

    async def load(
        self,
        *,
        user_id: str,
        cache_key: str,
    ) -> Mapping[str, Any] | None:
        try:
            value = await get_db()[self.collection_name].find_one(
                {"user_id": user_id, "cache_key": cache_key},
                {"_id": 0, "result": 1},
            )
        except Exception as exc:
            raise QueryGenerationCacheError(
                "query-generation cache could not be read"
            ) from exc
        result = value.get("result") if value else None
        return result if isinstance(result, Mapping) else None

    async def save(
        self,
        *,
        user_id: str,
        cache_key: str,
        result: Mapping[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc)
        try:
            await get_db()[self.collection_name].update_one(
                {"user_id": user_id, "cache_key": cache_key},
                {
                    "$set": {
                        "result": dict(result),
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
            raise QueryGenerationCacheError(
                "query-generation cache could not be written"
            ) from exc
