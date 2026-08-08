from __future__ import annotations

from typing import Any

from ..observability import record_analysis_trace
from ..models import (
    AnalysisIssue,
    AnalysisRequest,
    EvidencePackage,
    IssueCode,
    IssueSeverity,
    IssueStage,
    RetrievalResult,
)
from ..repositories import (
    EvidenceRepository,
    EvidenceRepositoryError,
    HydrationSourceBatch,
)
from ..services import EvidenceHydrator, deduplicate_table_references
from ..state import AnalysisPhase, DataAnalysisState


from scripts.data_analysis_agent.runtime.observability.logging import (
    get_analysis_logger,
)


logger = get_analysis_logger(__name__)


def build_hydration_node(
    repository: EvidenceRepository,
    hydrator: EvidenceHydrator | None = None,
) -> Any:
    """Load authoritative sources and publish checkpoint-safe evidence handles."""

    selected_hydrator = hydrator or EvidenceHydrator()

    async def hydrate(state: DataAnalysisState) -> dict[str, Any]:
        request = AnalysisRequest.model_validate(state["request"])
        retrieval = RetrievalResult.model_validate(state["retrieval_result"])
        references = deduplicate_table_references(retrieval.table_references)

        if not references:
            record_analysis_trace(
                metrics={
                    "hydration_status": "empty",
                    "hydration_requested_table_count": 0,
                    "hydrated_table_count": 0,
                },
                tags=("hydration:empty",),
            )
            return {
                "phase": AnalysisPhase.HYDRATED,
                "evidence_package": EvidencePackage(
                    run_id=state["run_id"],
                    status="empty",
                    retrieved_table_count=0,
                    hydrated_table_count=0,
                ),
            }

        pdf_references = tuple(
            reference
            for reference in references
            if reference.source_type == "pdf_table"
        )
        try:
            sources = (
                await repository.load_sources(
                    user_id=request.user_id,
                    document_ids=request.document_ids,
                    table_ids=tuple(
                        reference.table_id for reference in pdf_references
                    ),
                )
                if pdf_references
                else HydrationSourceBatch(tables=(), documents=())
            )
        except EvidenceRepositoryError:
            logger.exception("Evidence hydration failed for run %s", state["run_id"])
            record_analysis_trace(
                metrics={
                    "hydration_status": "failed",
                    "hydration_requested_table_count": len(references),
                    "hydrated_table_count": 0,
                    "analysis_failed_stage": IssueStage.HYDRATION,
                },
                tags=("hydration:error",),
            )
            return {
                "phase": AnalysisPhase.FAILED,
                "evidence_package": EvidencePackage(
                    run_id=state["run_id"],
                    status="failed",
                    retrieved_table_count=len(references),
                    hydrated_table_count=0,
                ),
                "errors": [
                    AnalysisIssue(
                        code=IssueCode.HYDRATION_FAILED,
                        severity=IssueSeverity.ERROR,
                        stage=IssueStage.HYDRATION,
                        message="Authoritative table evidence could not be loaded.",
                        retryable=True,
                    )
                ],
            }

        outcome = selected_hydrator.hydrate(
            run_id=state["run_id"],
            user_id=request.user_id,
            document_ids=request.selected_source_ids,
            references=references,
            sources=sources,
            pinned_datasets=request.pinned_datasets,
            workspace_id=request.workspace_id,
        )
        record_analysis_trace(
            metrics={
                "hydration_status": outcome.package.status,
                "hydration_requested_table_count": len(references),
                "hydrated_table_count": outcome.package.hydrated_table_count,
                "hydration_unresolved_table_count": len(
                    outcome.package.unresolved_tables
                ),
            },
            tags=(f"hydration:{outcome.package.status}",),
        )
        return {
            "phase": AnalysisPhase.HYDRATED,
            "evidence_package": outcome.package,
            "warnings": list(outcome.warnings),
        }

    return hydrate
