from __future__ import annotations

from typing import Any

from ..models import (
    AnalysisRequest,
    AnalysisRequirements,
    AugmentedEvidence,
    DatasetProfiles,
    EvidenceAssessment,
    EvidencePackage,
)
from ..observability import record_analysis_trace
from ..services.preparation import DatasetPreparationRunner
from ..state import AnalysisPhase, DataAnalysisState


def build_preparation_node(runner: DatasetPreparationRunner) -> Any:
    """Prepare only final-ready evidence while keeping every row external."""

    async def prepare_datasets(state: DataAnalysisState) -> dict[str, Any]:
        request = AnalysisRequest.model_validate(state["request"])
        augmented_value = state.get("augmented_evidence")
        augmented = (
            AugmentedEvidence.model_validate(augmented_value)
            if augmented_value is not None
            else None
        )
        outcome = await runner.run(
            run_id=state["run_id"],
            user_id=request.user_id,
            document_ids=request.document_ids,
            requirements=AnalysisRequirements.model_validate(
                state["analysis_requirements"]
            ),
            assessment=EvidenceAssessment.model_validate(
                state["evidence_assessment"]
            ),
            evidence=EvidencePackage.model_validate(state["evidence_package"]),
            profiles=DatasetProfiles.model_validate(state["dataset_profiles"]),
            augmented=augmented,
        )
        artifact = outcome.artifact
        record_analysis_trace(
            metrics={
                "normalization_status": artifact.status,
                "normalization_can_analyze": artifact.can_analyze,
                "normalization_selected_dataset_count": (
                    artifact.selected_dataset_count
                ),
                "normalization_prepared_dataset_count": (
                    artifact.prepared_dataset_count
                ),
                "normalization_rejected_dataset_count": len(
                    artifact.rejected_dataset_ids
                ),
                "normalization_selected_fact_count": len(
                    artifact.selected_fact_ids
                ),
                "normalization_selected_derived_dataset_count": len(
                    artifact.selected_derived_dataset_ids
                ),
                "normalization_cache_hit_count": artifact.cache_hit_count,
                "normalization_passthrough_count": artifact.passthrough_count,
                "normalization_materialized_count": artifact.materialized_count,
                "normalization_failure_count": len(artifact.failures),
                "normalization_input_row_count": artifact.total_input_rows,
                "normalization_output_row_count": artifact.total_output_rows,
                "normalization_duplicate_row_count": sum(
                    item.duplicate_row_count for item in artifact.datasets
                ),
                "normalization_footnote_row_count": sum(
                    item.footnote_row_count for item in artifact.datasets
                ),
                "normalization_transformation_count": sum(
                    len(item.transformations) for item in artifact.datasets
                ),
                "normalization_total_latency_ms": outcome.total_latency_ms,
                "normalization_dataset_latencies_ms": (
                    outcome.dataset_latencies_ms
                ),
            },
            tags=(
                f"normalization:{artifact.status.value}",
                (
                    "normalization:ready"
                    if artifact.can_analyze
                    else "normalization:blocked"
                ),
            ),
        )
        return {
            "phase": (
                AnalysisPhase.PREPARED
                if artifact.can_analyze
                else AnalysisPhase.FAILED
            ),
            "normalization_result": artifact,
            "warnings": list(outcome.warnings),
            "errors": list(outcome.errors),
        }

    return prepare_datasets
