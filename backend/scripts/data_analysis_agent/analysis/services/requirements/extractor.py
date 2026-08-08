from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Protocol

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from scripts.data_analysis_agent.runtime.observability import measure_llm_call

from ...models.requirements import (
    REQUIREMENTS_PROMPT_VERSION,
    RequirementsExtraction,
)
from ...models.request import AnalysisRequest


REQUIREMENTS_SYSTEM_PROMPT = """You extract a data-analysis request into a strict
structured schema. Return analytical intent, independently assessable requirements,
groupings, expected granularity, join need, evidence modality, and whether every
selected document must be covered.

Preserve every explicit metric, entity, period, dimension, filter, unit, and constraint.
Mark a requirement optional only when the user makes it optional. Never invent an
entity, period, unit, metric, or filter. Closely related financial concepts are not
aliases: revenue is not income, earnings, profit, or cash flow. Aliases must be strictly
equivalent (for example, earnings per share and EPS).

Attach entity_names to a metric when the request requires that metric separately for
specific entities. Use table_evidence_required for exact multi-value calculations,
comparisons, trends, correlations, anomaly detection, rankings, or aggregations. Text
may still be acceptable as supporting evidence. Set requires_all_selected_documents
only when the request explicitly compares or covers all selected documents.

Do not answer the request and do not produce an analysis plan."""

REQUIREMENTS_JSON_INSTRUCTIONS = """Return one JSON object with this exact shape:
{
  "operation": "comparison|trend|aggregation|correlation|anomaly_detection|ranking|distribution|lookup|summarization|other",
  "requirements": [{
    "kind": "metric|entity|period|dimension|unit|filter|topic",
    "name": "string",
    "aliases": ["strictly equivalent alias"],
    "required": true,
    "expected_data_type": "number|string|boolean|date|any",
    "unit": null,
    "entity_names": [],
    "filter_operator": null,
    "filter_values": []
  }],
  "groupings": [],
  "expected_granularity": null,
  "requires_join": false,
  "requires_all_selected_documents": false,
  "table_evidence_required": false,
  "text_evidence_acceptable": true
}
For a filter, filter_operator must be one of equals, not_equals, greater_than,
greater_than_or_equal, less_than, less_than_or_equal, in, contains, or between.
For non-filter requirements, use null filter_operator and an empty filter_values list."""


class AsyncRequirementsGenerator(Protocol):
    async def ainvoke(self, input: Any, **kwargs: Any) -> Any: ...


def requirements_model_name() -> str:
    return os.getenv(
        "DATA_ANALYSIS_REQUIREMENTS_MODEL",
        "google/gemini-2.5-flash-lite",
    )


@lru_cache(maxsize=1)
def get_requirements_llm() -> AsyncRequirementsGenerator:
    llm = ChatOpenAI(
        model=requirements_model_name(),
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=0,
        max_retries=1,
        max_tokens=int(os.getenv("DATA_ANALYSIS_REQUIREMENTS_MAX_TOKENS", "2200")),
        timeout=float(os.getenv("DATA_ANALYSIS_REQUIREMENTS_TIMEOUT", "30")),
    )
    # JSON mode avoids provider-side grammar explosions from the richer internal
    # Pydantic schema while retaining local Pydantic validation and retry behavior.
    return llm.with_structured_output(
        RequirementsExtraction,
        method="json_mode",
    )


class RequirementsExtractor:
    """One focused LLM call with a single structured-output recovery attempt."""

    def __init__(
        self,
        generator: AsyncRequirementsGenerator | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self._generator = generator
        self.model = model or requirements_model_name()

    async def extract(
        self,
        request: AnalysisRequest,
    ) -> tuple[RequirementsExtraction, int]:
        generator = self._generator or get_requirements_llm()
        payload = {
            "user_request": request.query,
            "selected_source_ids": list(request.selected_source_ids),
            "selected_source_count": len(request.selected_source_ids),
        }
        messages: list[Any] = [
            SystemMessage(
                content=(
                    REQUIREMENTS_SYSTEM_PROMPT
                    + "\n\n"
                    + REQUIREMENTS_JSON_INSTRUCTIONS
                )
            ),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
        attempts = min(
            2,
            max(1, int(os.getenv("DATA_ANALYSIS_REQUIREMENTS_ATTEMPTS", "2"))),
        )
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with measure_llm_call(
                    stage="requirements_extraction",
                    model=self.model,
                    prompt_version=REQUIREMENTS_PROMPT_VERSION,
                ):
                    response = await generator.ainvoke(messages)
                parsed = (
                    response
                    if isinstance(response, RequirementsExtraction)
                    else RequirementsExtraction.model_validate(response)
                )
                return parsed, attempt
            except (
                OutputParserException,
                ValidationError,
                ValueError,
                TypeError,
            ) as exc:
                last_error = exc
                if attempt < attempts:
                    messages.append(
                        SystemMessage(
                            content=(
                                "Return one complete object matching the schema. "
                                "Do not add prose or omit required fields."
                            )
                        )
                    )
            except Exception as exc:
                last_error = exc
                break
        raise RuntimeError("requirements extraction failed") from last_error
