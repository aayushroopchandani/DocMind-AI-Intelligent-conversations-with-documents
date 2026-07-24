from __future__ import annotations

from typing import Any

from ..models import (
    AnalysisRequest,
    AnalysisRequirements,
    DatasetProfiles,
    EvidenceAssessment,
    EvidencePackage,
    RetrievalResult,
)
from ..observability import record_analysis_trace
from ..services import EvidenceCompletionRunner
from ..state import AnalysisPhase, DataAnalysisState


def build_completion_node(runner: EvidenceCompletionRunner) -> Any:
    """Complete missing evidence through the bounded Phase 6 cascade."""

    async def complete_evidence(state: DataAnalysisState) -> dict[str, Any]:
        outcome = await runner.run(
            run_id=state["run_id"],
            request=AnalysisRequest.model_validate(state["request"]),
            requirements=AnalysisRequirements.model_validate(
                state["analysis_requirements"]
            ),
            retrieval=RetrievalResult.model_validate(state["retrieval_result"]),
            evidence=EvidencePackage.model_validate(state["evidence_package"]),
            profiles=DatasetProfiles.model_validate(state["dataset_profiles"]),
            assessment=EvidenceAssessment.model_validate(
                state["evidence_assessment"]
            ),
        )
        artifact = outcome.artifact
        record_analysis_trace(
            metrics={
                "evidence_completion_status": artifact.status,
                "evidence_completion_final_decision": artifact.final_decision,
                "rescued_dataset_count": len(artifact.added_datasets),
                "validated_text_fact_count": len(artifact.facts),
                "derived_dataset_count": len(artifact.derived_datasets),
                "completion_attempt_count": len(artifact.attempts),
                "completion_rejected_evidence_count": len(
                    artifact.rejected_evidence
                ),
                "completion_remaining_requirement_count": len(
                    artifact.remaining_requirement_ids
                ),
                "completion_cache_hit_count": sum(
                    item.cache_hit for item in artifact.attempts
                ),
                "targeted_retrieval_attempt_count": sum(
                    item.stage.value == "targeted_retrieval"
                    for item in artifact.attempts
                ),
                "targeted_repair_query_count": sum(
                    len(item.queries) for item in artifact.attempts
                ),
                "completion_failed_attempt_count": sum(
                    item.outcome.value == "failed"
                    for item in artifact.attempts
                ),
            },
            tags=(
                f"completion:{artifact.status.value}",
                f"completion-decision:{artifact.final_decision}",
            ),
        )
        return {
            "phase": AnalysisPhase.COMPLETED,
            "augmented_evidence": artifact,
            "evidence_assessment": outcome.assessment,
            "warnings": list(outcome.warnings),
        }

    return complete_evidence
