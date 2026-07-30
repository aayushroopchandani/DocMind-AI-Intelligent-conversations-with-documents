from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from .runs import AnalysisRunPhase, AnalysisRunStatus


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
MAX_EVENT_PAYLOAD_BYTES = 64 * 1024


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AnalysisEventType(str, Enum):
    RUN_CREATED = "run_created"
    RUN_STARTED = "run_started"
    CONTEXT_RESOLUTION_STARTED = "context_resolution_started"
    CONTEXT_RESOLVED = "context_resolved"
    ARTIFACT_REGISTERED = "artifact_registered"
    DATASET_REGISTERED = "dataset_registered"
    RETRIEVAL_STARTED = "retrieval_started"
    RETRIEVAL_COMPLETED = "retrieval_completed"
    REQUIREMENTS_STARTED = "requirements_started"
    REQUIREMENTS_COMPLETED = "requirements_completed"
    EVIDENCE_HYDRATED = "evidence_hydrated"
    DATASETS_PROFILED = "datasets_profiled"
    EVIDENCE_ASSESSED = "evidence_assessed"
    EVIDENCE_COMPLETED = "evidence_completed"
    DATASET_PREPARED = "dataset_prepared"
    CLARIFICATION_REQUIRED = "clarification_required"
    CANCELLATION_REQUESTED = "cancellation_requested"
    RUN_CANCELLED = "run_cancelled"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_EXPIRED = "run_expired"
    RUN_RECOVERED = "run_recovered"


class AnalysisRunEvent(BaseModel):
    """One small, append-only event suitable for Mongo replay and SSE."""

    schema_version: Literal[1] = 1
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str = Field(min_length=36, max_length=36)
    user_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=1)
    event_type: AnalysisEventType
    status: AnalysisRunStatus | None = None
    phase: AnalysisRunPhase | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    deduplication_key: str | None = Field(default=None, max_length=200)
    trace_id: str | None = Field(default=None, max_length=200)
    occurred_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    @field_validator("event_id", "run_id", mode="before")
    @classmethod
    def normalize_uuid(cls, value: object) -> str:
        try:
            return str(UUID(str(value or "").strip()))
        except (ValueError, AttributeError) as exc:
            raise ValueError("event and run IDs must be valid UUIDs") from exc

    @field_validator(
        "user_id",
        "workspace_id",
        "deduplication_key",
        "trace_id",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER_RE.fullmatch(value):
            raise ValueError("identifier contains unsupported characters")
        return value

    @field_validator("payload")
    @classmethod
    def validate_payload_size(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError("event payload exceeds 64 KiB")
        return value


__all__ = [
    "AnalysisEventType",
    "AnalysisRunEvent",
    "MAX_EVENT_PAYLOAD_BYTES",
]
