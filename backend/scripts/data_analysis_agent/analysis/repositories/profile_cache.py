from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Protocol, Sequence

from pydantic import ValidationError
from pymongo import UpdateOne

from db.mongodb import get_db

from ..models import DatasetProfile, profile_cache_key


logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProfileCacheError(RuntimeError):
    """Raised when the optional dataset-profile cache is unavailable."""


class ProfileCache(Protocol):
    async def load_many(
        self,
        *,
        user_id: str,
        cache_keys: Sequence[str],
    ) -> dict[str, DatasetProfile]: ...

    async def save_many(
        self,
        *,
        user_id: str,
        profiles: Sequence[DatasetProfile],
    ) -> None: ...


class MongoProfileCache:
    """Versioned, tenant-scoped cache for deterministic dataset profiles."""

    async def load_many(
        self,
        *,
        user_id: str,
        cache_keys: Sequence[str],
    ) -> dict[str, DatasetProfile]:
        unique_keys = tuple(dict.fromkeys(cache_keys))
        if not unique_keys:
            return {}
        try:
            documents = await get_db().dataset_profiles.find(
                {
                    "user_id": user_id,
                    "cache_key": {"$in": list(unique_keys)},
                },
                {"_id": 0, "cache_key": 1, "profile": 1},
            ).to_list(length=len(unique_keys))
        except Exception as exc:
            raise ProfileCacheError("dataset profiles could not be read") from exc

        profiles: dict[str, DatasetProfile] = {}
        for document in documents:
            cache_key = str(document.get("cache_key") or "")
            try:
                profile = DatasetProfile.model_validate(document.get("profile"))
            except ValidationError:
                logger.warning("Ignoring invalid cached dataset profile %s", cache_key)
                continue
            if cache_key:
                profiles[cache_key] = profile
        return profiles

    async def save_many(
        self,
        *,
        user_id: str,
        profiles: Sequence[DatasetProfile],
    ) -> None:
        if not profiles:
            return
        now = _utc_now()
        operations = []
        for profile in profiles:
            cache_key = profile_cache_key(
                dataset_id=profile.dataset_id,
                source_version=profile.source_version,
                profiler_version=profile.profiler_version,
            )
            operations.append(
                UpdateOne(
                    {"user_id": user_id, "cache_key": cache_key},
                    {
                        "$set": {
                            "profile": profile.model_dump(mode="json"),
                            "updated_at": now,
                        },
                        "$setOnInsert": {
                            "user_id": user_id,
                            "cache_key": cache_key,
                            "dataset_id": profile.dataset_id,
                            "source_version": profile.source_version,
                            "profiler_version": profile.profiler_version,
                            "created_at": now,
                        },
                    },
                    upsert=True,
                )
            )
        try:
            await get_db().dataset_profiles.bulk_write(operations, ordered=False)
        except Exception as exc:
            raise ProfileCacheError("dataset profiles could not be cached") from exc
