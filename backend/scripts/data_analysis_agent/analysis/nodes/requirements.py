from __future__ import annotations

from typing import Any

from ..models import AnalysisRequest
from ..observability import record_analysis_trace
from ..services.requirements import AnalysisRequirementsRunner
from ..state import DataAnalysisState


def build_requirements_node(runner: AnalysisRequirementsRunner) -> Any:
    """Extract requirements independently so it can run beside retrieval."""

    async def extract_requirements(state: DataAnalysisState) -> dict[str, Any]:
        request = AnalysisRequest.model_validate(state["request"])
        outcome = await runner.run(request)
        artifact = outcome.artifact
        record_analysis_trace(
            metrics={
                "requirements_cache_hit": artifact.diagnostics.cache_hit,
                "requirements_extraction_attempts": (
                    artifact.diagnostics.extraction_attempts
                ),
                "requirements_fallback": artifact.diagnostics.used_fallback,
                "requirements_operation": artifact.operation,
                "requirements_count": len(artifact.requirements),
                "required_requirements_count": sum(
                    item.required for item in artifact.requirements
                ),
                "requirements_validation_adjustment_count": len(
                    artifact.diagnostics.validation_adjustments
                ),
                "requirements_validation_conflict_count": len(
                    artifact.diagnostics.validation_conflicts
                ),
                "requires_all_selected_documents": (
                    artifact.requires_all_selected_documents
                ),
                "table_evidence_required": artifact.table_evidence_required,
            },
            tags=(
                f"operation:{artifact.operation.value}",
                (
                    "requirements-cache:hit"
                    if artifact.diagnostics.cache_hit
                    else "requirements-cache:miss"
                ),
                (
                    "requirements:fallback"
                    if artifact.diagnostics.used_fallback
                    else "requirements:llm"
                ),
            ),
        )
        return {
            "analysis_requirements": outcome.artifact,
            "warnings": list(outcome.warnings),
        }

    return extract_requirements
