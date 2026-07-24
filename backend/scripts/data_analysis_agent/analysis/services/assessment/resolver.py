from __future__ import annotations

import json
import os
from enum import Enum
from functools import lru_cache
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

from .matcher import AmbiguityCandidate


AMBIGUITY_SYSTEM_PROMPT = """Resolve only whether each requested analytical concept
is represented by its candidate table or column. Use strict semantic equivalence.
Related financial measures are not interchangeable: revenue, gross profit, operating
income, net income, earnings, and cash flow are distinct. Return no_match when they are
merely related, and ambiguous when the compact metadata is insufficient.

You receive only table metadata, inferred types, units, and example values. Never infer
that unseen rows contain a requested value. The payload contains a deduplicated tables
map and pairs that reference it by table_key. Do not answer the user's analysis
request."""


class AmbiguityDecision(str, Enum):
    MATCH = "match"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"


class AmbiguityResolution(BaseModel):
    pair_id: str = Field(min_length=1)
    decision: AmbiguityDecision
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=300)

    model_config = ConfigDict(extra="forbid")


class AmbiguityResolutionBatch(BaseModel):
    resolutions: tuple[AmbiguityResolution, ...] = Field(max_length=20)

    model_config = ConfigDict(extra="forbid")


class AsyncAmbiguityGenerator(Protocol):
    async def ainvoke(self, input: Any, **kwargs: Any) -> Any: ...


def ambiguity_model_name() -> str:
    return os.getenv(
        "DATA_ANALYSIS_AMBIGUITY_MODEL",
        "google/gemini-2.5-flash-lite",
    )


@lru_cache(maxsize=1)
def get_ambiguity_llm() -> AsyncAmbiguityGenerator:
    llm = ChatOpenAI(
        model=ambiguity_model_name(),
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=0,
        max_retries=1,
        max_tokens=int(os.getenv("DATA_ANALYSIS_AMBIGUITY_MAX_TOKENS", "1800")),
        timeout=float(os.getenv("DATA_ANALYSIS_AMBIGUITY_TIMEOUT", "25")),
    )
    return llm.with_structured_output(AmbiguityResolutionBatch)


class AmbiguityResolver:
    """Resolve all bounded ambiguous pairs in one compact structured call."""

    def __init__(
        self,
        generator: AsyncAmbiguityGenerator | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self._generator = generator
        self.model = model or ambiguity_model_name()

    async def resolve(
        self,
        candidates: tuple[AmbiguityCandidate, ...],
    ) -> dict[str, AmbiguityResolution]:
        if not candidates:
            return {}
        tables: dict[str, dict[str, Any]] = {}
        pairs = []
        for candidate in candidates:
            table_key = candidate.evidence.table_id or candidate.evidence.dataset_id
            if table_key and table_key not in tables:
                tables[table_key] = {
                    "table_title": candidate.table_title,
                    "table_summary": candidate.table_summary[:900],
                    "columns": [
                        {
                            "key": column.key,
                            "label": column.label,
                            "declared_type": column.declared_type,
                            "inferred_type": column.inferred_type.value,
                            "semantic_role": column.semantic_role.value,
                            "unit": column.detected_unit,
                            "example_values": list(column.example_values),
                        }
                        for column in candidate.columns[:30]
                    ],
                }
            pairs.append(
                {
                    "pair_id": candidate.pair_id,
                    "requirement": {
                        "kind": candidate.requirement.kind.value,
                        "name": candidate.requirement.name,
                        "aliases": list(candidate.requirement.aliases),
                        "expected_data_type": (
                            candidate.requirement.expected_data_type.value
                        ),
                        "unit": candidate.requirement.unit,
                    },
                    "candidate": {
                        "table_key": table_key,
                        "candidate_label": candidate.evidence.label,
                        "candidate_column_key": candidate.evidence.column_key,
                    },
                }
            )
        generator = self._generator or get_ambiguity_llm()
        response = await generator.ainvoke(
            [
                SystemMessage(content=AMBIGUITY_SYSTEM_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        {"tables": tables, "pairs": pairs},
                        ensure_ascii=False,
                    )
                ),
            ]
        )
        parsed = (
            response
            if isinstance(response, AmbiguityResolutionBatch)
            else AmbiguityResolutionBatch.model_validate(response)
        )
        allowed_ids = {candidate.pair_id for candidate in candidates}
        return {
            resolution.pair_id: resolution
            for resolution in parsed.resolutions
            if resolution.pair_id in allowed_ids
        }
