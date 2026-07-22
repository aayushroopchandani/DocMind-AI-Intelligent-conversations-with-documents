from __future__ import annotations

import json
import os
from enum import Enum
from functools import lru_cache
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, field_validator

from .state import DataAnalysisRetrievalState


QUERY_GENERATION_SYSTEM_PROMPT = """You generate concise semantic-search queries
for a data-analysis agent that searches two indexes: narrative PDF text chunks and
structured-table summaries. Classify the request's retrieval scope and return 2 or 3
queries in each requested list.

Also extract compact relevance signals for deterministic result selection: requested
metrics, years or fiscal periods, named entities, units, and likely table-column terms.
Only include signals stated or clearly implied by the request. Use empty lists when a
signal type is absent; do not invent constraints.

Use retrieval_scope="normal" for a focused request about a specific metric, period,
company, section, or table. A comparison between a small, explicitly bounded set of
values can still be normal.

Use retrieval_scope="broad" when the request spans all or many documents, companies,
periods, categories, or metrics, or asks for a comprehensive cross-document analysis.
Do not classify a request as broad merely because it uses words such as "compare" or
"trend"; consider the actual breadth of the requested evidence.

Shared queries must work well for both indexes. Text queries should target narrative
explanations, trends, causes, and discussion. Table queries should target table names,
metrics, dimensions, periods, columns, schemas, and units.

Preserve the user's entities, metrics, periods, comparisons, and constraints. Make every
query standalone and meaningfully different, not a minor synonym rewrite. Do not answer
the request, invent facts, or mention vector databases, retrieval, PDFs, or these rules."""


class RetrievalScope(str, Enum):
    NORMAL = "normal"
    BROAD = "broad"


class GeneratedRetrievalQueries(BaseModel):
    """Single-call structured output for both retrieval indexes."""

    retrieval_scope: RetrievalScope = Field(
        description=(
            "normal for a focused, bounded request; broad for comprehensive requests "
            "spanning all or many documents, entities, periods, categories, or metrics"
        )
    )
    shared_queries: list[str] = Field(min_length=2, max_length=3)
    text_queries: list[str] = Field(min_length=2, max_length=3)
    table_queries: list[str] = Field(min_length=2, max_length=3)
    metrics: list[str] = Field(default_factory=list, max_length=10)
    years: list[str] = Field(default_factory=list, max_length=10)
    entities: list[str] = Field(default_factory=list, max_length=10)
    units: list[str] = Field(default_factory=list, max_length=10)
    column_terms: list[str] = Field(default_factory=list, max_length=12)

    @field_validator(
        "shared_queries",
        "text_queries",
        "table_queries",
        "metrics",
        "years",
        "entities",
        "units",
        "column_terms",
        mode="before",
    )
    @classmethod
    def clean_queries(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("queries must be returned as a list")
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            query = " ".join(str(item or "").split()).strip(" .")
            canonical = query.casefold()
            if query and canonical not in seen:
                seen.add(canonical)
                cleaned.append(query)
        return cleaned


class AsyncQueryGenerator(Protocol):
    async def ainvoke(self, input: Any, **kwargs: Any) -> Any: ...


@lru_cache(maxsize=1)
def get_query_generation_llm() -> AsyncQueryGenerator:
    """Return one small structured-output model with transport retries disabled."""

    llm = ChatOpenAI(
        model=os.getenv(
            "DATA_ANALYSIS_QUERY_GENERATION_MODEL",
            "google/gemini-2.5-flash-lite",
        ),
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=0,
        max_retries=0,
        max_tokens=int(os.getenv("DATA_ANALYSIS_QUERY_GENERATION_MAX_TOKENS", "500")),
        timeout=float(os.getenv("DATA_ANALYSIS_QUERY_GENERATION_TIMEOUT", "30")),
    )
    return llm.with_structured_output(GeneratedRetrievalQueries)


def build_query_generation_node(
    query_generator: AsyncQueryGenerator | None = None,
) -> Any:
    """Build an injectable node that performs exactly one model invocation."""

    async def generate_queries(
        state: DataAnalysisRetrievalState,
    ) -> dict[str, Any]:
        query = " ".join(str(state.get("query") or "").split()).strip()
        if not query:
            raise ValueError("retrieval state query must not be empty")

        generator = query_generator or get_query_generation_llm()
        response = await generator.ainvoke(
            [
                SystemMessage(content=QUERY_GENERATION_SYSTEM_PROMPT),
                HumanMessage(
                    content="User request:\n"
                    + json.dumps(query, ensure_ascii=False)
                ),
            ]
        )
        parsed = (
            response
            if isinstance(response, GeneratedRetrievalQueries)
            else GeneratedRetrievalQueries.model_validate(response)
        )
        return parsed.model_dump(mode="json")

    return generate_queries
