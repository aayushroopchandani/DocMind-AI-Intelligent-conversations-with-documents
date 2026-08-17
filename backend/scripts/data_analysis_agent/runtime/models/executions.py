"""Durable execution records (Phase 9.8.1).

One `AnalysisExecution` per execution attempt of a plan. It is the thing a
recovered worker reads to answer "was this already done, and how far did it
get?", so it holds identity, versions and bounded metrics — never output rows,
sample values or large diagnostics. Those live in blob storage and are
referenced from here (9.9.4).

The record is also the reservation. It is written before any artifact upload
starts, so a crash between upload and commit leaves a row that reconciliation
can find, rather than an orphaned object nobody remembers creating.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifacts import BlobReference
from .plans import PlanColumn


EXECUTION_RECORD_VERSION = "1.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionStatus(str, Enum):
    RESERVED = "reserved"
    """The record exists and claims the execution key; no work has published."""

    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_EXECUTION_STATUSES = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
    }
)


class StageStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class CheckpointRecord(BaseModel):
    """A reusable stage result (9.8.3).

    A recovered worker may reuse a checkpoint only when its *complete* key still
    matches: same stage recipe, same inputs, same engine and semantics. Any
    drift and the stage is recomputed, because a checkpoint keyed on less than
    that could hand back a result the current plan would never have produced.
    """

    stage_id: str = Field(min_length=1, max_length=120)
    stage_recipe_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_signatures: tuple[str, ...] = Field(default=(), max_length=30)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_version: str = Field(min_length=1, max_length=60)
    semantics_version: str = Field(min_length=1, max_length=60)
    schema_columns: tuple[PlanColumn, ...] = Field(default=(), max_length=500)
    row_count: int = Field(ge=0)
    blob: BlobReference | None = None
    created_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid", frozen=True)

    def reusable_for(
        self,
        *,
        stage_recipe_hash: str,
        input_signatures: tuple[str, ...],
        engine_version: str,
        semantics_version: str,
    ) -> bool:
        """Return whether this checkpoint still matches the work being asked."""

        return (
            self.stage_recipe_hash == stage_recipe_hash
            and self.input_signatures == input_signatures
            and self.engine_version == engine_version
            and self.semantics_version == semantics_version
            and self.blob is not None
        )


class StageRecord(BaseModel):
    """Bounded per-stage summary. Diagnostics stay out of MongoDB."""

    stage_id: str = Field(min_length=1, max_length=120)
    step_ids: tuple[str, ...] = Field(default=(), max_length=64)
    status: StageStatus = StageStatus.PENDING
    input_rows: int = Field(default=0, ge=0)
    output_rows: int = Field(default=0, ge=0)
    output_columns: int = Field(default=0, ge=0)
    duration_ms: float = Field(default=0, ge=0)
    checkpoint: CheckpointRecord | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class ResultArtifacts(BaseModel):
    """References to the durable result bundle (9.9.3).

    MongoDB stores these pointers; the bytes live in the blob store.
    """

    rows: BlobReference
    schema_manifest: BlobReference
    lineage: BlobReference
    preview: BlobReference

    model_config = ConfigDict(extra="forbid", frozen=True)

    def references(self) -> tuple[BlobReference, ...]:
        return (self.rows, self.schema_manifest, self.lineage, self.preview)


class ExecutionMetrics(BaseModel):
    """Bounded counters, safe to keep in the record and to emit on SSE."""

    input_rows: int = Field(default=0, ge=0)
    output_rows: int = Field(default=0, ge=0)
    output_columns: int = Field(default=0, ge=0)
    output_bytes: int = Field(default=0, ge=0)
    stages_completed: int = Field(default=0, ge=0)
    stages_reused: int = Field(default=0, ge=0)
    duration_ms: float = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisExecution(BaseModel):
    """One durable execution attempt of one plan."""

    record_version: str = EXECUTION_RECORD_VERSION
    execution_id: str = Field(min_length=1, max_length=120)
    execution_key: str = Field(pattern=r"^[0-9a-f]{64}$")

    user_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=36, max_length=36)
    plan_id: str = Field(min_length=36, max_length=36)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    input_signatures: tuple[str, ...] = Field(default=(), max_length=30)
    recipe_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_version: str = Field(min_length=1, max_length=60)
    semantics_version: str = Field(min_length=1, max_length=60)

    status: ExecutionStatus = ExecutionStatus.RESERVED
    current_stage_id: str | None = Field(default=None, max_length=120)
    stages: tuple[StageRecord, ...] = Field(default=(), max_length=64)

    # The run's lease attempt at the moment this record was claimed. Only a
    # worker holding this token may publish (9.8.5).
    fencing_token: int = Field(default=0, ge=0)
    worker_id: str | None = Field(default=None, max_length=200)

    result_content_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    result_columns: tuple[PlanColumn, ...] = Field(default=(), max_length=500)
    artifacts: ResultArtifacts | None = None
    metrics: ExecutionMetrics = Field(default_factory=ExecutionMetrics)

    failure_code: str | None = Field(default=None, max_length=80)
    failure_message: str | None = Field(default=None, max_length=1_000)
    warnings: tuple[str, ...] = Field(default=(), max_length=50)

    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_execution(self) -> Self:
        terminal = self.status in TERMINAL_EXECUTION_STATUSES
        if terminal != (self.finished_at is not None):
            raise ValueError(
                "a terminal execution must record finished_at, and only then"
            )
        if self.status is ExecutionStatus.SUCCEEDED:
            if self.result_content_hash is None or self.artifacts is None:
                raise ValueError(
                    "a succeeded execution needs a hashed, published result"
                )
            if not self.result_columns:
                raise ValueError("a succeeded execution needs an output schema")
        if self.status is ExecutionStatus.FAILED and not self.failure_code:
            raise ValueError("a failed execution requires a typed failure code")
        if self.failure_code is not None and self.status not in {
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
        }:
            raise ValueError("only failed or cancelled executions carry a failure")
        if self.finished_at is not None and self.started_at is None:
            raise ValueError("a finished execution must have started")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_EXECUTION_STATUSES

    def checkpoint_for(self, stage_id: str) -> CheckpointRecord | None:
        for stage in self.stages:
            if stage.stage_id == stage_id:
                return stage.checkpoint
        return None


__all__ = [
    "EXECUTION_RECORD_VERSION",
    "TERMINAL_EXECUTION_STATUSES",
    "AnalysisExecution",
    "CheckpointRecord",
    "ExecutionMetrics",
    "ExecutionStatus",
    "ResultArtifacts",
    "StageRecord",
    "StageStatus",
]
