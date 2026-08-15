from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from ..privacy.gateway import PrivacyGateway

from .runs import AnalysisRunPhase, AnalysisRunStatus


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
MAX_EVENT_PAYLOAD_BYTES = 64 * 1024
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "cell",
        "cells",
        "chunk",
        "chunks",
        "content",
        "formula",
        "formulas",
        "prompt",
        "raw",
        "row",
        "rows",
        "secure_url",
        "signed_url",
        "text",
        "value",
        "values",
    }
)
_REDACTED_TEXT_KEYS = frozenset(
    {"comment", "description", "message", "reason"}
)


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
    PLANNING_STARTED = "planning_started"
    PLAN_GENERATED = "plan_generated"
    PLAN_VALIDATION_STARTED = "plan_validation_started"
    PLAN_VALIDATION_FAILED = "plan_validation_failed"
    PLAN_REPAIR_STARTED = "plan_repair_started"
    PLAN_READY = "plan_ready"
    PLAN_APPROVAL_REQUIRED = "plan_approval_required"
    PLAN_APPROVED = "plan_approved"
    PLAN_REJECTED = "plan_rejected"
    EXECUTION_QUEUED = "execution_queued"
    PATCH_APPROVAL_REQUIRED = "patch_approval_required"
    PATCH_APPROVED = "patch_approved"
    PATCH_REJECTED = "patch_rejected"
    CLARIFICATION_REQUIRED = "clarification_required"
    PAUSE_REQUESTED = "pause_requested"
    RUN_PAUSED = "run_paused"
    RUN_RESUMED = "run_resumed"
    RUN_RESUMED_AS_NEW = "run_resumed_as_new"
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
        sanitized = _sanitize_event_value(value)
        assert isinstance(sanitized, dict)
        encoded = json.dumps(
            sanitized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError("event payload exceeds 64 KiB")
        return sanitized


def _sanitize_event_value(
    value: JsonValue,
    *,
    parent_key: str | None = None,
) -> JsonValue:
    if isinstance(value, dict):
        output: dict[str, JsonValue] = {}
        for key, item in value.items():
            normalized_key = str(key).strip().casefold()
            if normalized_key in _FORBIDDEN_PAYLOAD_KEYS:
                raise ValueError(
                    f"event payload cannot contain raw data field '{normalized_key}'"
                )
            output[str(key)] = _sanitize_event_value(
                item,
                parent_key=normalized_key,
            )
        return output
    if isinstance(value, list):
        return [
            _sanitize_event_value(item, parent_key=parent_key)
            for item in value
        ]
    if isinstance(value, str) and parent_key in _REDACTED_TEXT_KEYS:
        return PrivacyGateway.redact_sensitive_text(value)
    return value


__all__ = [
    "AnalysisEventType",
    "AnalysisRunEvent",
    "MAX_EVENT_PAYLOAD_BYTES",
]
