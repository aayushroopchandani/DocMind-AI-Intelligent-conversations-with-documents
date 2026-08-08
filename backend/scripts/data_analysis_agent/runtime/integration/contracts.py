from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, Literal, Protocol

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from scripts.data_analysis_agent.analysis.state import AnalysisPhase
from scripts.data_analysis_agent.analysis.models import (
    AnalysisRequirements,
    DatasetProfiles,
    NormalizationResult,
)
from scripts.data_analysis_agent.runtime.models import (
    AnalysisEventType,
    AnalysisRunOutcome,
    AnalysisRunPhase,
    RunIssueSummary,
    StageTokenUsage,
    TokenUsage,
)
from .metadata import phase7_model_versions, phase7_prompt_versions


CancellationCheck = Callable[[], Awaitable[bool]]
Phase7Outcome = Literal[
    AnalysisRunOutcome.DATASETS_PREPARED,
    AnalysisRunOutcome.CLARIFICATION_REQUIRED,
    AnalysisRunOutcome.UNANSWERABLE,
    AnalysisRunOutcome.FAILED,
]


class StreamingAnalysisGraph(Protocol):
    """The narrow LangGraph surface required by the durable adapter."""

    def astream(
        self,
        input: Mapping[str, Any],
        config: RunnableConfig | None = None,
        *,
        stream_mode: Literal["values"],
        **kwargs: Any,
    ) -> AsyncIterator[Mapping[str, Any]]: ...


class Phase7Progress(BaseModel):
    """Small control-plane event; source rows must never be placed here."""

    event_type: AnalysisEventType
    phase: AnalysisRunPhase
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    deduplication_key: str = Field(min_length=1, max_length=200)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class Phase7ProgressReporter(Protocol):
    async def emit(self, progress: Phase7Progress) -> None: ...


class NullPhase7ProgressReporter:
    async def emit(self, progress: Phase7Progress) -> None:
        del progress


class Phase7PlanningArtifacts(BaseModel):
    """Bounded row-free artifacts required by the Phase 8 planner."""

    requirements: AnalysisRequirements
    dataset_profiles: DatasetProfiles
    normalization: NormalizationResult

    model_config = ConfigDict(extra="forbid", frozen=True)


class Phase7ExecutionResult(BaseModel):
    """Bounded execution summary returned to the durable run worker."""

    outcome: Phase7Outcome
    graph_phase: AnalysisPhase
    final_dataset_ids: tuple[str, ...] = Field(default=(), max_length=100)
    source_dataset_ids: tuple[str, ...] = Field(default=(), max_length=100)
    selected_fact_ids: tuple[str, ...] = Field(default=(), max_length=100)
    selected_derived_dataset_ids: tuple[str, ...] = Field(
        default=(),
        max_length=100,
    )
    prepared_dataset_count: int = Field(default=0, ge=0)
    total_input_rows: int = Field(default=0, ge=0)
    total_output_rows: int = Field(default=0, ge=0)
    warnings: tuple[RunIssueSummary, ...] = Field(default=(), max_length=100)
    errors: tuple[RunIssueSummary, ...] = Field(default=(), max_length=100)
    model_versions: dict[str, str] = Field(
        default_factory=phase7_model_versions
    )
    prompt_versions: dict[str, str] = Field(
        default_factory=phase7_prompt_versions
    )
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    token_usage_by_stage: dict[str, StageTokenUsage] = Field(default_factory=dict)
    planning_artifacts: Phase7PlanningArtifacts | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_outcome(self) -> "Phase7ExecutionResult":
        if self.outcome == AnalysisRunOutcome.DATASETS_PREPARED:
            if self.graph_phase != AnalysisPhase.PREPARED:
                raise ValueError("prepared outcomes require a prepared graph")
        elif self.outcome == AnalysisRunOutcome.FAILED:
            if not self.errors:
                raise ValueError("failed outcomes require an error summary")
        elif self.final_dataset_ids or self.prepared_dataset_count:
            raise ValueError(
                "non-prepared outcomes cannot expose prepared datasets"
            )
        return self


class Phase7ExecutionCancelled(Exception):
    """Cooperative cancellation observed between graph updates."""


class Phase7InputError(ValueError):
    """The durable run and its pinned immutable datasets disagree."""


__all__ = [
    "CancellationCheck",
    "NullPhase7ProgressReporter",
    "Phase7ExecutionCancelled",
    "Phase7ExecutionResult",
    "Phase7InputError",
    "Phase7Outcome",
    "Phase7PlanningArtifacts",
    "Phase7Progress",
    "Phase7ProgressReporter",
    "StreamingAnalysisGraph",
]
