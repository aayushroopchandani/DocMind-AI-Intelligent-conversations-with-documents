from __future__ import annotations

from enum import Enum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from ..execution.admission import ExecutionAdmission
from ..models.capabilities import ExecutorCapabilities
from ..models.plans import AnalysisPlan
from ..models.events import AnalysisEventType
from ..models.runs import (
    AnalysisRunPhase,
    RunIssueSummary,
    StageTokenUsage,
    TokenUsage,
)


class PlanValidationLayer(str, Enum):
    STRUCTURAL = "structural"
    REFERENTIAL = "referential"
    TYPE_AND_UNIT = "type_and_unit"
    EXECUTION_POLICY = "execution_policy"
    RESOURCE = "resource"
    CONCURRENCY = "concurrency"
    PROVENANCE = "provenance"


class PlanValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class PlanValidationIssue(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    layer: PlanValidationLayer
    severity: PlanValidationSeverity
    message: str = Field(min_length=1, max_length=500)
    path: str = Field(default="", max_length=300)
    repairable: bool = True

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class PlanValidationReport(BaseModel):
    issues: tuple[PlanValidationIssue, ...] = Field(default=(), max_length=200)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @property
    def errors(self) -> tuple[PlanValidationIssue, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.severity == PlanValidationSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[PlanValidationIssue, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.severity == PlanValidationSeverity.WARNING
        )

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def repairable(self) -> bool:
        return bool(self.errors) and all(issue.repairable for issue in self.errors)


class PlanResourcePolicy(BaseModel):
    max_context_bytes: int = Field(default=2 * 1024 * 1024, ge=16 * 1024)
    max_plan_bytes: int = Field(default=1024 * 1024, ge=16 * 1024)
    max_steps: int = Field(default=32, ge=1, le=64)
    max_rows_scanned: int = Field(default=2_000_000, ge=1)
    max_cells_written: int = Field(default=250_000, ge=1)
    max_joins: int = Field(default=4, ge=0, le=16)
    max_python_memory_mb: int = Field(default=512, ge=64)
    max_python_seconds: float = Field(default=120, gt=0)
    max_estimated_cost_usd: float = Field(default=1.0, ge=0)
    max_generated_rows: int = Field(default=100_000, ge=1)
    max_chart_cardinality: int = Field(default=500, ge=1)
    plan_approval_python_seconds: float = Field(default=15, ge=0)
    plan_approval_cost_usd: float = Field(default=0.10, ge=0)
    plan_approval_generated_rows: int = Field(default=25_000, ge=1)
    # Phase 9.1.3 selective early approval. A workbook write wider than this,
    # or a plan carrying more invented assumptions than this, is gated even
    # when it is cheap and non-destructive.
    plan_approval_cells_written: int = Field(default=10_000, ge=1)
    plan_approval_assumptions: int = Field(default=2, ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_approval_thresholds(self) -> "PlanResourcePolicy":
        if self.plan_approval_python_seconds > self.max_python_seconds:
            raise ValueError("Python approval threshold cannot exceed its limit")
        if self.plan_approval_cost_usd > self.max_estimated_cost_usd:
            raise ValueError("cost approval threshold cannot exceed its limit")
        if self.plan_approval_generated_rows > self.max_generated_rows:
            raise ValueError("generated-row approval threshold exceeds its limit")
        if self.plan_approval_cells_written > self.max_cells_written:
            raise ValueError("cell approval threshold cannot exceed its limit")
        return self


class PlanningOutcome(str, Enum):
    PLAN_READY = "plan_ready"
    APPROVAL_REQUIRED = "approval_required"
    CLARIFICATION_REQUIRED = "clarification_required"
    FAILED = "failed"


class PlanningProgress(BaseModel):
    event_type: AnalysisEventType
    phase: AnalysisRunPhase
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    deduplication_key: str = Field(min_length=1, max_length=200)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class PlanningProgressReporter(Protocol):
    async def emit(self, progress: PlanningProgress) -> None: ...


class NullPlanningProgressReporter:
    async def emit(self, progress: PlanningProgress) -> None:
        del progress


class PlanningExecutionResult(BaseModel):
    outcome: PlanningOutcome
    plan: AnalysisPlan | None = None
    # What the runtime may do with `plan`, decided by the planning service
    # because only it holds the active capability profile. See
    # runtime/execution/admission.py.
    admission: ExecutionAdmission = ExecutionAdmission.PLAN_ONLY
    reports: tuple[PlanValidationReport, ...] = Field(default=(), max_length=2)
    warnings: tuple[RunIssueSummary, ...] = Field(default=(), max_length=100)
    errors: tuple[RunIssueSummary, ...] = Field(default=(), max_length=100)
    clarification: str | None = Field(default=None, max_length=1_000)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    token_usage_by_stage: dict[str, StageTokenUsage] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_outcome(self) -> "PlanningExecutionResult":
        has_plan = self.plan is not None
        if self.outcome in {
            PlanningOutcome.PLAN_READY,
            PlanningOutcome.APPROVAL_REQUIRED,
        }:
            if not has_plan or not self.reports or not self.reports[-1].valid:
                raise ValueError("ready planning outcomes need a valid plan")
        elif has_plan:
            raise ValueError("unsuccessful planning outcomes cannot expose a plan")
        if self.outcome == PlanningOutcome.CLARIFICATION_REQUIRED:
            if not self.clarification:
                raise ValueError("clarification outcome requires a question")
        if self.outcome == PlanningOutcome.FAILED and not self.errors:
            raise ValueError("failed planning outcomes require errors")
        return self


__all__ = [
    "ExecutorCapabilities",
    "PlanResourcePolicy",
    "PlanValidationIssue",
    "PlanValidationLayer",
    "PlanValidationReport",
    "PlanValidationSeverity",
    "PlanningProgress",
    "PlanningProgressReporter",
    "NullPlanningProgressReporter",
    "PlanningExecutionResult",
    "PlanningOutcome",
]
