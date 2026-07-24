from __future__ import annotations

from typing import Any

from ..models import AnalysisRequest
from ..services.requirements import AnalysisRequirementsRunner
from ..state import DataAnalysisState


def build_requirements_node(runner: AnalysisRequirementsRunner) -> Any:
    """Extract requirements independently so it can run beside retrieval."""

    async def extract_requirements(state: DataAnalysisState) -> dict[str, Any]:
        request = AnalysisRequest.model_validate(state["request"])
        outcome = await runner.run(request)
        return {
            "analysis_requirements": outcome.artifact,
            "warnings": list(outcome.warnings),
        }

    return extract_requirements
