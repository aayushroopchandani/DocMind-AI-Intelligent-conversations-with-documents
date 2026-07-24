from __future__ import annotations

from typing import Any

from ..models import (
    AnalysisRequest,
    AnalysisRequirements,
    DatasetProfiles,
    EvidencePackage,
    RetrievalResult,
)
from ..services.assessment import EvidenceAssessmentRunner
from ..state import AnalysisPhase, DataAnalysisState


def build_assessment_node(runner: EvidenceAssessmentRunner) -> Any:
    """Assess profiled evidence after both parallel branches have completed."""

    async def assess_evidence(state: DataAnalysisState) -> dict[str, Any]:
        outcome = await runner.run(
            request=AnalysisRequest.model_validate(state["request"]),
            requirements=AnalysisRequirements.model_validate(
                state["analysis_requirements"]
            ),
            retrieval=RetrievalResult.model_validate(state["retrieval_result"]),
            evidence=EvidencePackage.model_validate(state["evidence_package"]),
            profiles=DatasetProfiles.model_validate(state["dataset_profiles"]),
        )
        return {
            "phase": AnalysisPhase.ASSESSED,
            "evidence_assessment": outcome.artifact,
            "warnings": list(outcome.warnings),
        }

    return assess_evidence
