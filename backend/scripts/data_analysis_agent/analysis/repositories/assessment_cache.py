from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from pydantic import ValidationError

from db.mongodb import get_db

from ..models.assessment import EvidenceAssessment


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AssessmentCacheError(RuntimeError):
    """Raised when the optional evidence-assessment cache is unavailable."""


class AssessmentCache(Protocol):
    async def load(
        self,
        *,
        user_id: str,
        cache_key: str,
    ) -> EvidenceAssessment | None: ...

    async def save(
        self,
        *,
        user_id: str,
        cache_key: str,
        assessment: EvidenceAssessment,
    ) -> None: ...


class MongoAssessmentCache:
    """Tenant-scoped cache that also avoids repeated ambiguity LLM calls."""

    async def load(
        self,
        *,
        user_id: str,
        cache_key: str,
    ) -> EvidenceAssessment | None:
        try:
            document = await get_db().evidence_assessments_cache.find_one(
                {"user_id": user_id, "cache_key": cache_key},
                {"_id": 0, "assessment": 1},
            )
        except Exception as exc:
            raise AssessmentCacheError("evidence assessment could not be read") from exc
        if not document:
            return None
        try:
            return EvidenceAssessment.model_validate(document.get("assessment"))
        except ValidationError:
            return None

    async def save(
        self,
        *,
        user_id: str,
        cache_key: str,
        assessment: EvidenceAssessment,
    ) -> None:
        now = _utc_now()
        try:
            await get_db().evidence_assessments_cache.update_one(
                {"user_id": user_id, "cache_key": cache_key},
                {
                    "$set": {
                        "assessment": assessment.model_dump(mode="json"),
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
            raise AssessmentCacheError(
                "evidence assessment could not be cached"
            ) from exc
