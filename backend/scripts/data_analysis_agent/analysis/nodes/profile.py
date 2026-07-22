from __future__ import annotations

from typing import Any

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
            document_ids=request.document_ids,
            evidence=evidence,
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
