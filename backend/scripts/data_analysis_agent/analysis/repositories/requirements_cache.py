from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from pydantic import ValidationError

from db.mongodb import get_db

from ..models.requirements import AnalysisRequirements


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RequirementsCacheError(RuntimeError):
    """Raised when the optional requirements cache is unavailable."""


class RequirementsCache(Protocol):
    async def load(
        self,
        *,
        user_id: str,
        cache_key: str,
    ) -> AnalysisRequirements | None: ...

    async def save(
        self,
        *,
        user_id: str,
        cache_key: str,
        requirements: AnalysisRequirements,
    ) -> None: ...


class MongoRequirementsCache:
    """Tenant-scoped cache for validated, versioned requirement artifacts."""

    async def load(
        self,
        *,
        user_id: str,
        cache_key: str,
    ) -> AnalysisRequirements | None:
        try:
            document = await get_db().analysis_requirements_cache.find_one(
                {"user_id": user_id, "cache_key": cache_key},
                {"_id": 0, "requirements": 1},
            )
        except Exception as exc:
            raise RequirementsCacheError("analysis requirements could not be read") from exc
        if not document:
            return None
        try:
            return AnalysisRequirements.model_validate(document.get("requirements"))
        except ValidationError:
            return None

    async def save(
        self,
        *,
        user_id: str,
        cache_key: str,
        requirements: AnalysisRequirements,
    ) -> None:
        now = _utc_now()
        try:
            await get_db().analysis_requirements_cache.update_one(
                {"user_id": user_id, "cache_key": cache_key},
                {
                    "$set": {
                        "requirements": requirements.model_dump(mode="json"),
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
            raise RequirementsCacheError(
                "analysis requirements could not be cached"
            ) from exc
