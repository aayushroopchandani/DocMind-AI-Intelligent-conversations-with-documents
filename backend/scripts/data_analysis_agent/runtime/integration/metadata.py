from __future__ import annotations

import os

from scripts.data_analysis_agent.analysis.models import (
    AMBIGUITY_PROMPT_VERSION,
    REQUIREMENTS_PROMPT_VERSION,
    TEXT_EVIDENCE_PROMPT_VERSION,
)
from scripts.data_analysis_agent.retrieval.query_generation import (
    QUERY_GENERATION_PROMPT_VERSION,
)


def phase7_model_versions() -> dict[str, str]:
    """Configured LLM identities that can participate in a Phase 1–7 run."""

    return {
        "query_generation": os.getenv(
            "DATA_ANALYSIS_QUERY_GENERATION_MODEL",
            "google/gemini-2.5-flash-lite",
        ),
        "requirements": os.getenv(
            "DATA_ANALYSIS_REQUIREMENTS_MODEL",
            "google/gemini-2.5-flash-lite",
        ),
        "ambiguity_resolution": os.getenv(
            "DATA_ANALYSIS_AMBIGUITY_MODEL",
            "google/gemini-2.5-flash-lite",
        ),
        "text_evidence": os.getenv(
            "DATA_ANALYSIS_TEXT_EVIDENCE_MODEL",
            "google/gemini-2.5-flash-lite",
        ),
    }


def phase7_prompt_versions() -> dict[str, str]:
    """Version every LLM prompt that can affect the prepared datasets."""

    return {
        "query_generation": QUERY_GENERATION_PROMPT_VERSION,
        "requirements": REQUIREMENTS_PROMPT_VERSION,
        "ambiguity_resolution": AMBIGUITY_PROMPT_VERSION,
        "text_evidence": TEXT_EVIDENCE_PROMPT_VERSION,
    }


__all__ = [
    "phase7_model_versions",
    "phase7_prompt_versions",
]
