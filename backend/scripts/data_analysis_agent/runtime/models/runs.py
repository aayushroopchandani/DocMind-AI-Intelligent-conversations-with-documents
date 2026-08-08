from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .privacy import AnalysisPrivacyMode, PrivacySummary


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AnalysisMode(str, Enum):
    ASK = "ask"
    ANALYSE = "analyse"
    EDIT = "edit"


class AnalysisRunStatus(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    WAITING = "waiting"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AnalysisRunPhase(str, Enum):
    CONTEXT_RESOLUTION = "context_resolution"
    EVIDENCE_PREPARATION = "evidence_preparation"
    REQUIREMENTS = "requirements"
    NORMALIZATION = "normalization"
    PLANNING = "planning"
    PLAN_VALIDATION = "plan_validation"
    APPROVAL = "approval"
    EXECUTION = "execution"
    RESULT_VALIDATION = "result_validation"
    PROPOSAL = "proposal"
    APPLICATION = "application"
    COMPLETED = "completed"


class AnalysisRunOutcome(str, Enum):
    DATASETS_PREPARED = "datasets_prepared"
    CLARIFICATION_REQUIRED = "clarification_required"
    UNANSWERABLE = "unanswerable"
    PLAN_READY = "plan_ready"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class RunApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


TERMINAL_RUN_STATUSES = frozenset(
    {
        AnalysisRunStatus.SUCCEEDED,
        AnalysisRunStatus.FAILED,
        AnalysisRunStatus.CANCELLED,
        AnalysisRunStatus.EXPIRED,
    }
)


class DatasetVersionReference(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=200)
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @field_validator("source_version", mode="before")
    @classmethod
    def normalize_version(cls, value: object) -> str:
        normalized = str(value or "").strip().casefold()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("source_version must be a lowercase SHA-256 digest")
        return normalized


class RunIssueSummary(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=500)
    count: int = Field(default=1, ge=1)
    retryable: bool = False

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class TokenUsage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return self


class StageTokenUsage(BaseModel):
    """Reproducible token/cost accounting for one logical LLM stage."""

    stage: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=120)
    pricing_version: str = Field(default="unconfigured", min_length=1, max_length=120)
    pricing_configured: bool = False
    call_count: int = Field(default=1, ge=1)
    duration_ms: float = Field(default=0, ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class AnalysisRun(BaseModel):
    """Durable, tenant-scoped control-plane state for one analysis request."""

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=36, max_length=36)
    user_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    chat_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=200)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: AnalysisMode
    prompt: str = Field(min_length=1, max_length=20_000)
    privacy_mode: AnalysisPrivacyMode = AnalysisPrivacyMode.STANDARD
    privacy_summary: PrivacySummary = Field(default_factory=PrivacySummary)
    active_artifact_id: str | None = Field(default=None, max_length=200)
    status: AnalysisRunStatus = AnalysisRunStatus.CREATED
    phase: AnalysisRunPhase = AnalysisRunPhase.CONTEXT_RESOLUTION
    outcome: AnalysisRunOutcome | None = None
    # Spreadsheet inputs are synchronized through external durable stores
    # after the run_created transaction. Workers may only claim this run once
    # the resulting immutable artifact/dataset references are attached.
    inputs_ready: bool = True

    cancellation_requested: bool = False
    cancellation_requested_at: datetime | None = None
    pause_requested: bool = False
    pause_requested_at: datetime | None = None
    paused_at: datetime | None = None
    checkpoint_id: str | None = Field(default=None, max_length=200)
    last_completed_step_id: str | None = Field(default=None, max_length=200)
    resume_count: int = Field(default=0, ge=0)
    paused_from_status: AnalysisRunStatus | None = None
    paused_from_phase: AnalysisRunPhase | None = None
    paused_from_outcome: AnalysisRunOutcome | None = None

    # A terminal run is never reopened. "Resume" from a terminal run creates
    # a fresh run and records immutable lineage back to the original request.
    parent_run_id: str | None = Field(default=None, max_length=36)
    root_run_id: str | None = Field(default=None, max_length=36)
    worker_id: str | None = Field(default=None, max_length=200)
    lease_expires_at: datetime | None = None
    lease_attempt: int = Field(default=0, ge=0)

    version: int = Field(default=0, ge=0)
    last_event_sequence: int = Field(default=0, ge=0)

    input_artifact_version_ids: tuple[str, ...] = Field(default=(), max_length=100)
    input_dataset_versions: tuple[DatasetVersionReference, ...] = Field(
        default=(),
        max_length=100,
    )
    selected_document_ids: tuple[str, ...] = Field(default=(), max_length=20)
    final_artifact_ids: tuple[str, ...] = Field(default=(), max_length=100)
    final_dataset_ids: tuple[str, ...] = Field(default=(), max_length=100)
    current_plan_id: str | None = Field(default=None, max_length=36)
    current_plan_revision: int | None = Field(default=None, ge=1)
    current_plan_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    plan_approval_status: RunApprovalStatus | None = None

    warnings_summary: tuple[RunIssueSummary, ...] = Field(default=(), max_length=100)
    errors_summary: tuple[RunIssueSummary, ...] = Field(default=(), max_length=100)
    model_versions: dict[str, str] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    component_versions: dict[str, str] = Field(default_factory=dict)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    token_usage_by_stage: dict[str, StageTokenUsage] = Field(default_factory=dict)
    timings_ms: dict[str, float] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    @field_validator("run_id", "parent_run_id", "root_run_id", mode="before")
    @classmethod
    def normalize_run_id(cls, value: object) -> str | None:
        if value is None:
            return None
        try:
            return str(UUID(str(value or "").strip()))
        except (ValueError, AttributeError) as exc:
            raise ValueError("run_id must be a valid UUID") from exc

    @field_validator("request_fingerprint", mode="before")
    @classmethod
    def normalize_request_fingerprint(cls, value: object) -> str:
        normalized = str(value or "").strip().casefold()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("request_fingerprint must be a lowercase SHA-256 digest")
        return normalized

    @field_validator(
        "user_id",
        "workspace_id",
        "chat_id",
        "active_artifact_id",
        "worker_id",
        "checkpoint_id",
        "last_completed_step_id",
        "current_plan_id",
        "input_artifact_version_ids",
        "final_artifact_ids",
        "final_dataset_ids",
    )
    @classmethod
    def validate_identifiers(cls, value: object) -> object:
        values = value if isinstance(value, tuple) else (value,)
        for item in values:
            if item is not None and not _IDENTIFIER_RE.fullmatch(str(item)):
                raise ValueError("identifier contains unsupported characters")
        return value

    @field_validator(
        "input_artifact_version_ids",
        "input_dataset_versions",
        "selected_document_ids",
        "final_artifact_ids",
        "final_dataset_ids",
        mode="before",
    )
    @classmethod
    def deduplicate_sequences(cls, value: object) -> object:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("value must be a list or tuple")
        output: list[object] = []
        seen: set[str] = set()
        for item in value:
            identity = (
                repr(sorted(item.items()))
                if isinstance(item, dict)
                else repr(item)
            )
            if identity not in seen:
                seen.add(identity)
                output.append(item)
        return tuple(output)

    @field_validator("model_versions", "prompt_versions", "component_versions")
    @classmethod
    def validate_version_map(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 50:
            raise ValueError("version maps are limited to 50 entries")
        for key, version in value.items():
            if not key.strip() or len(key) > 120:
                raise ValueError("version map keys must be non-empty and bounded")
            if not version.strip() or len(version) > 200:
                raise ValueError("version map values must be non-empty and bounded")
        return value

    @field_validator("token_usage_by_stage")
    @classmethod
    def validate_stage_usage(
        cls,
        value: dict[str, StageTokenUsage],
    ) -> dict[str, StageTokenUsage]:
        if len(value) > 50:
            raise ValueError("token_usage_by_stage is limited to 50 entries")
        if any(key != record.stage for key, record in value.items()):
            raise ValueError("stage token-usage keys must match their records")
        return value

    @field_validator("timings_ms")
    @classmethod
    def validate_timings(cls, value: dict[str, float]) -> dict[str, float]:
        if len(value) > 100:
            raise ValueError("timings_ms is limited to 100 entries")
        for key, duration in value.items():
            if not key.strip() or len(key) > 120:
                raise ValueError("timing keys must be non-empty and bounded")
            if duration < 0:
                raise ValueError("timings cannot be negative")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.privacy_summary.mode != self.privacy_mode:
            raise ValueError("privacy summary mode must match the run")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at cannot precede created_at")
        if self.completed_at is not None and self.started_at is not None:
            if self.completed_at < self.started_at:
                raise ValueError("completed_at cannot precede started_at")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")

        if self.cancellation_requested != (
            self.cancellation_requested_at is not None
        ):
            raise ValueError(
                "cancellation_requested and cancellation_requested_at must agree"
            )
        if self.pause_requested != (self.pause_requested_at is not None):
            raise ValueError("pause_requested and pause_requested_at must agree")
        if self.pause_requested and self.cancellation_requested:
            raise ValueError("pause and cancellation cannot both be requested")
        if (self.worker_id is None) != (self.lease_expires_at is None):
            raise ValueError("worker_id and lease_expires_at must be set together")
        if self.worker_id is not None and self.lease_attempt < 1:
            raise ValueError("a leased run must have lease_attempt >= 1")
        if self.worker_id is not None and not self.inputs_ready:
            raise ValueError("a run cannot be leased before its inputs are ready")
        if (
            self.status
            in {
                AnalysisRunStatus.ACTIVE,
                AnalysisRunStatus.WAITING,
                AnalysisRunStatus.PAUSED,
                AnalysisRunStatus.SUCCEEDED,
            }
            and not self.inputs_ready
        ):
            raise ValueError(
                f"{self.status.value} runs require initialized inputs"
            )

        terminal = self.status in TERMINAL_RUN_STATUSES
        if terminal:
            if self.outcome is None or self.completed_at is None:
                raise ValueError("terminal runs need an outcome and completed_at")
            if self.worker_id is not None:
                raise ValueError("terminal runs cannot retain an execution lease")
        elif self.completed_at is not None:
            raise ValueError("non-terminal runs cannot have completed_at")

        paused_metadata = (
            self.paused_at,
            self.paused_from_status,
            self.paused_from_phase,
        )
        if self.status == AnalysisRunStatus.PAUSED:
            if any(value is None for value in paused_metadata):
                raise ValueError("paused runs need checkpoint lifecycle metadata")
            if self.worker_id is not None or self.pause_requested:
                raise ValueError("paused runs cannot retain a lease or pause request")
            if self.outcome is not None:
                raise ValueError("paused runs cannot have a current outcome")
            if self.paused_from_status not in {
                AnalysisRunStatus.CREATED,
                AnalysisRunStatus.ACTIVE,
                AnalysisRunStatus.WAITING,
            }:
                raise ValueError("paused runs need a resumable prior status")
        elif (
            any(value is not None for value in paused_metadata)
            or self.paused_from_outcome is not None
        ):
            raise ValueError("only paused runs may retain paused lifecycle metadata")

        expected_outcome = {
            AnalysisRunStatus.FAILED: AnalysisRunOutcome.FAILED,
            AnalysisRunStatus.CANCELLED: AnalysisRunOutcome.CANCELLED,
            AnalysisRunStatus.EXPIRED: AnalysisRunOutcome.EXPIRED,
        }.get(self.status)
        if expected_outcome is not None and self.outcome != expected_outcome:
            raise ValueError(f"{self.status.value} runs need {expected_outcome.value}")
        if self.status == AnalysisRunStatus.WAITING:
            if self.outcome not in {
                AnalysisRunOutcome.CLARIFICATION_REQUIRED,
                AnalysisRunOutcome.PLAN_READY,
            }:
                raise ValueError("waiting runs need clarification or approval")
            if (
                self.outcome == AnalysisRunOutcome.PLAN_READY
                and (
                    self.phase != AnalysisRunPhase.APPROVAL
                    or self.plan_approval_status != RunApprovalStatus.PENDING
                )
            ):
                raise ValueError("approval waits require a pending plan")
        elif (
            not terminal
            and self.status != AnalysisRunStatus.PAUSED
            and self.outcome is not None
        ):
            raise ValueError("active runs cannot have a terminal outcome")
        if (self.parent_run_id is None) != (self.root_run_id is None):
            raise ValueError("resumed run lineage must include parent and root IDs")
        if self.parent_run_id == self.run_id or self.root_run_id == self.run_id:
            raise ValueError("a run cannot be its own lineage ancestor")
        plan_values = (
            self.current_plan_id,
            self.current_plan_revision,
            self.current_plan_hash,
            self.plan_approval_status,
        )
        if any(value is not None for value in plan_values) != all(
            value is not None for value in plan_values
        ):
            raise ValueError("current plan identity and approval must be complete")
        if self.outcome == AnalysisRunOutcome.PLAN_READY:
            if self.plan_approval_status not in {
                RunApprovalStatus.NOT_REQUIRED,
                RunApprovalStatus.PENDING,
                RunApprovalStatus.APPROVED,
            }:
                raise ValueError("plan-ready runs require a usable plan")
        if self.outcome == AnalysisRunOutcome.REJECTED:
            if self.plan_approval_status != RunApprovalStatus.REJECTED:
                raise ValueError("rejected runs require a rejected plan")
        return self


__all__ = [
    "AnalysisMode",
    "AnalysisRun",
    "AnalysisRunOutcome",
    "AnalysisRunPhase",
    "AnalysisRunStatus",
    "DatasetVersionReference",
    "RunIssueSummary",
    "RunApprovalStatus",
    "StageTokenUsage",
    "TERMINAL_RUN_STATUSES",
    "TokenUsage",
]
