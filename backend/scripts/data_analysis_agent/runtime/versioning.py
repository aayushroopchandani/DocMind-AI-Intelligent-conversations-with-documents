"""Stable component versions persisted with every durable analysis run."""

from __future__ import annotations

from scripts.data_analysis_agent.analysis.models.preparation import (
    DATASET_NORMALIZER_VERSION,
)
from scripts.data_analysis_agent.analysis.models.profile import (
    DATASET_PROFILER_VERSION,
)

from .models.plans import PLAN_VALIDATOR_VERSION, PLAN_VERSION


def phase8_component_versions() -> dict[str, str]:
    return {
        "plan_schema": PLAN_VERSION,
        "plan_validator": PLAN_VALIDATOR_VERSION,
        "dataset_profiler": DATASET_PROFILER_VERSION,
        "dataset_normalizer": DATASET_NORMALIZER_VERSION,
    }


__all__ = ["phase8_component_versions"]
