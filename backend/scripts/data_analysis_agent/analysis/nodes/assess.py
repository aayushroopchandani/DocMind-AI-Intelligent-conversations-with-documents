from __future__ import annotations

from collections import Counter
from typing import Any

from ..models import (
    AnalysisRequest,
    AnalysisRequirements,
    DatasetProfiles,
    EvidencePackage,
    RetrievalResult,
)
from ..observability import record_analysis_trace
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
        artifact = outcome.artifact
        method_counts = Counter(
            reference.match_method.value
            for coverage in artifact.coverage
            for reference in coverage.evidence
        )
        supported_confidences = [
            coverage.confidence
            for coverage in artifact.coverage
            if coverage.status.value == "supported"
        ]
        record_analysis_trace(
            metrics={
                "assessment_cache_hit": artifact.diagnostics.cache_hit,
                "evidence_readiness_decision": artifact.decision,
                "coverage_required_count": artifact.required_count,
                "coverage_supported_count": artifact.supported_count,
                "coverage_partial_count": artifact.partial_count,
                "coverage_missing_count": artifact.missing_count,
                "coverage_conflicting_count": artifact.conflicting_count,
                "coverage_ambiguous_count": artifact.ambiguous_count,
                "ambiguity_llm_used": (
                    artifact.diagnostics.ambiguity_llm_used
                ),
                "ambiguity_candidate_count": (
                    artifact.diagnostics.ambiguity_candidate_count
                ),
                "ambiguity_resolved_count": (
                    artifact.diagnostics.ambiguity_resolved_count
                ),
                "deterministic_evidence_match_count": (
                    artifact.diagnostics.deterministic_match_count
                ),
                "llm_evidence_match_count": method_counts.get("llm", 0),
                "lexical_evidence_match_count": method_counts.get(
                    "lexical",
                    0,
                ),
                "minimum_supported_confidence": (
                    min(supported_confidences)
                    if supported_confidences
                    else None
                ),
            },
            tags=(
                f"readiness:{artifact.decision.value}",
                (
                    "assessment-cache:hit"
                    if artifact.diagnostics.cache_hit
                    else "assessment-cache:miss"
                ),
                (
                    "ambiguity-llm:used"
                    if artifact.diagnostics.ambiguity_llm_used
                    else "ambiguity-llm:skipped"
                ),
            ),
        )
        return {
            "phase": AnalysisPhase.ASSESSED,
            "evidence_assessment": outcome.artifact,
            "warnings": list(outcome.warnings),
        }

    return assess_evidence
