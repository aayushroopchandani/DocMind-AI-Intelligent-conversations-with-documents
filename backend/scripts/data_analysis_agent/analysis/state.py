from __future__ import annotations

import operator
from enum import Enum
from typing import Annotated, Literal, NotRequired, Required, TypedDict
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig

from .models import AnalysisIssue, AnalysisRequest, EvidencePackage, RetrievalResult


ANALYSIS_STATE_VERSION: Literal[1] = 1


class AnalysisPhase(str, Enum):
    INITIALIZED = "initialized"
    RETRIEVED = "retrieved"
    HYDRATED = "hydrated"
    FAILED = "failed"


class DataAnalysisState(TypedDict):
    """Minimal checkpoint state for the capabilities implemented today."""

    state_version: Required[Literal[1]]
    run_id: Required[str]
    request: Required[AnalysisRequest]
    phase: Required[AnalysisPhase]
    retrieval_result: NotRequired[RetrievalResult]
    evidence_package: NotRequired[EvidencePackage]
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
    document_ids: list[str] | tuple[str, ...],
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
            query=query,
            document_ids=document_ids,
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
        "configurable": {"thread_id": run_id},
        "metadata": {
            "agent": "data_analysis",
            "run_id": run_id,
            "chat_id": request.chat_id,
            "user_id": request.user_id,
        },
    }
