from __future__ import annotations

from typing import Any

from ..observability import record_analysis_trace
from ..models import AnalysisRequest, EvidencePackage
from ..services import DatasetProfilingRunner
from ..state import AnalysisPhase, DataAnalysisState


def build_profiling_node(runner: DatasetProfilingRunner) -> Any:
    """Profile hydrated datasets while keeping all source rows transient."""

    async def profile_datasets(state: DataAnalysisState) -> dict[str, Any]:
        request = AnalysisRequest.model_validate(state["request"])
        evidence = EvidencePackage.model_validate(state["evidence_package"])
        outcome = await runner.run(
            user_id=request.user_id,
            document_ids=request.selected_source_ids,
            evidence=evidence,
        )
        record_analysis_trace(
            metrics={
                "profiling_status": outcome.artifact.status,
                "profiling_requested_dataset_count": (
                    outcome.artifact.requested_count
                ),
                "profiled_dataset_count": outcome.artifact.profiled_count,
                "profile_failure_count": len(outcome.artifact.failures),
                "profile_cache_hit_count": outcome.artifact.cache_hit_count,
                "profile_generated_count": outcome.artifact.generated_count,
                "profile_cache_hit_ratio": (
                    outcome.artifact.cache_hit_count
                    / outcome.artifact.profiled_count
                    if outcome.artifact.profiled_count
                    else 0.0
                ),
            },
            tags=(f"profiling:{outcome.artifact.status}",),
        )
        return {
            "phase": (
                AnalysisPhase.FAILED
                if outcome.artifact.status == "failed"
                else AnalysisPhase.PROFILED
            ),
            "dataset_profiles": outcome.artifact,
            "warnings": list(outcome.warnings),
            "errors": list(outcome.errors),
        }

    return profile_datasets
