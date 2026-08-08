from __future__ import annotations

import operator
from enum import Enum
from typing import Annotated, Literal, NotRequired, Required, TypedDict
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig

from .models import (
    AugmentedEvidence,
    AnalysisIssue,
    AnalysisRequest,
    AnalysisRequirements,
    DatasetProfiles,
    EvidenceAssessment,
    EvidencePackage,
    NormalizationResult,
    RetrievalResult,
)
from scripts.data_analysis_agent.runtime.models.datasets import DatasetHandle
from scripts.data_analysis_agent.runtime.models.privacy import AnalysisPrivacyMode


ANALYSIS_STATE_VERSION: Literal[5] = 5


class AnalysisPhase(str, Enum):
    INITIALIZED = "initialized"
    RETRIEVED = "retrieved"
    HYDRATED = "hydrated"
    PROFILED = "profiled"
    ASSESSED = "assessed"
    COMPLETED = "completed"
    PREPARED = "prepared"
    FAILED = "failed"


class DataAnalysisState(TypedDict):
    """Minimal checkpoint state for the capabilities implemented today."""

    state_version: Required[Literal[5]]
    run_id: Required[str]
    request: Required[AnalysisRequest]
    phase: Required[AnalysisPhase]
    retrieval_result: NotRequired[RetrievalResult]
    evidence_package: NotRequired[EvidencePackage]
    dataset_profiles: NotRequired[DatasetProfiles]
    analysis_requirements: NotRequired[AnalysisRequirements]
    evidence_assessment: NotRequired[EvidenceAssessment]
    augmented_evidence: NotRequired[AugmentedEvidence]
    normalization_result: NotRequired[NormalizationResult]
    warnings: Annotated[list[AnalysisIssue], operator.add]
    errors: Annotated[list[AnalysisIssue], operator.add]


def _normalized_run_id(run_id: str | None) -> str:
    if run_id is None:
        return str(uuid4())
    try:
        return str(UUID(str(run_id).strip()))
    except (ValueError, AttributeError) as exc:
        raise ValueError("run_id must be a valid UUID") from exc


def create_analysis_state(
    *,
    user_id: str,
    chat_id: str,
    query: str,
    document_ids: list[str] | tuple[str, ...] = (),
    workspace_id: str | None = None,
    pinned_datasets: list[DatasetHandle] | tuple[DatasetHandle, ...] = (),
    privacy_mode: AnalysisPrivacyMode = AnalysisPrivacyMode.STANDARD,
    run_id: str | None = None,
) -> DataAnalysisState:
    """Validate a request and create isolated state for one analysis run."""

    normalized_run_id = _normalized_run_id(run_id)
    return DataAnalysisState(
        state_version=ANALYSIS_STATE_VERSION,
        run_id=normalized_run_id,
        request=AnalysisRequest(
            user_id=user_id,
            chat_id=chat_id,
            workspace_id=workspace_id or chat_id,
            query=query,
            document_ids=document_ids,
            pinned_datasets=pinned_datasets,
            privacy_mode=privacy_mode,
        ),
        phase=AnalysisPhase.INITIALIZED,
        warnings=[],
        errors=[],
    )


def analysis_thread_config(state: DataAnalysisState) -> RunnableConfig:
    """Isolate checkpoints by run while retaining chat metadata for tracing."""

    request = AnalysisRequest.model_validate(state["request"])
    run_id = _normalized_run_id(state["run_id"])
    return {
        "run_name": "data_analysis_agent",
        "tags": [
            "data-analysis",
            f"analysis-state-v{ANALYSIS_STATE_VERSION}",
        ],
        "configurable": {"thread_id": run_id},
        "metadata": {
            "agent": "data_analysis",
            "analysis_state_version": ANALYSIS_STATE_VERSION,
            "selected_document_count": len(request.document_ids),
            "pinned_dataset_count": len(request.pinned_datasets),
            "selected_source_count": len(request.selected_source_ids),
            "run_id": run_id,
            "chat_id": request.chat_id,
            "workspace_id": request.workspace_id,
            "user_id": request.user_id,
        },
    }
