from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from ...models.requirements import RequirementsExtraction
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
    return llm.with_structured_output(RequirementsExtraction)


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
            "selected_document_ids": list(request.document_ids),
            "selected_document_count": len(request.document_ids),
        }
        messages: list[Any] = [
            SystemMessage(content=REQUIREMENTS_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
        attempts = min(
            2,
            max(1, int(os.getenv("DATA_ANALYSIS_REQUIREMENTS_ATTEMPTS", "2"))),
        )
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await generator.ainvoke(messages)
                parsed = (
                    response
                    if isinstance(response, RequirementsExtraction)
                    else RequirementsExtraction.model_validate(response)
                )
                return parsed, attempt
            except (ValidationError, ValueError, TypeError) as exc:
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
