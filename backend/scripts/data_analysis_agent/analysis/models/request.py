from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


_DOCUMENT_ID_RE = re.compile(r"^[0-9a-f]{64}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AnalysisRequest(BaseModel):
    """Authenticated, immutable input for one data-analysis run."""

    user_id: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    document_ids: tuple[str, ...] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("user_id", "chat_id", "query", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        normalized = " ".join(str(value or "").split())
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("document_ids", mode="before")
    @classmethod
    def normalize_document_ids(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("document_ids must be a list or tuple")

        output: list[str] = []
        seen: set[str] = set()
        for item in value:
            document_id = str(item or "").strip().casefold()
            if not document_id or document_id in seen:
                continue
            if not _DOCUMENT_ID_RE.fullmatch(document_id):
                raise ValueError(
                    "analysis document_ids must be SHA-256 content identifiers"
                )
            seen.add(document_id)
            output.append(document_id)
        if not output:
            raise ValueError("at least one document_id is required")
        return tuple(output)
