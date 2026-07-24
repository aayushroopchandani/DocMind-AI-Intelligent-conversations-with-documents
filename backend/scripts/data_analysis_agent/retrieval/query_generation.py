from __future__ import annotations

import json
import os
import re
from enum import Enum
from functools import lru_cache
from typing import Any, Protocol

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator

from .state import DataAnalysisRetrievalState


QUERY_GENERATION_SYSTEM_PROMPT = """You generate concise semantic-search queries
for a data-analysis agent that searches two indexes: narrative PDF text chunks and
structured-table summaries. Classify the request's retrieval scope and return 2 or 3
queries in each requested list.

Also extract compact relevance signals for deterministic result selection: requested
metrics, years or fiscal periods, named entities, units, and likely table-column terms.
Only include signals stated or clearly implied by the request. Use empty lists when a
signal type is absent; do not invent constraints.

For every explicitly requested metric, entity, dimension, or substantive topic, return
one match_concept. Give it a canonical name and no more than three strictly equivalent
variants. Expand an abbreviation only when its meaning is unambiguous in this request.
Aliases within one concept must mean the same thing; do not add related metrics, generic
words such as "results" or "year", or answer facts. Put periods and measurement units
only in the years and units fields, not in match_concepts. Keep meaningful qualifiers
attached to their metric (for example, "geographic revenue" instead of "revenue").

Set table_intent="required" when answering requires structured rows/columns or an exact
multi-value comparison. Use "supporting" when a table would materially help a mixed
narrative and quantitative request. Use "none" for explanations, people, processes,
narrative accomplishments, and isolated facts that do not need tabular evidence.

Use retrieval_scope="normal" for a focused request about a specific metric, period,
company, section, or table. A comparison between a small, explicitly bounded set of
values can still be normal.

Use retrieval_scope="broad" when the request spans all or many documents, companies,
periods, categories, or metrics. A comprehensive request covering at least three
substantive evidence categories is broad even when it concerns one document.
Do not classify a request as broad merely because it uses words such as "compare" or
"trend"; consider the actual breadth of the requested evidence.

Shared queries must work well for both indexes. Text queries should target narrative
explanations, trends, causes, and discussion. Table queries should target table names,
metrics, dimensions, periods, columns, schemas, and units.

Preserve the user's entities, metrics, periods, comparisons, and constraints. Make every
query standalone and meaningfully different, not a minor synonym rewrite. Do not answer
the request, invent facts, or mention vector databases, retrieval, PDFs, or these rules."""

QUERY_GENERATION_JSON_INSTRUCTIONS = """Return one JSON object with this exact shape:
{
  "retrieval_scope": "normal|broad",
  "shared_queries": ["query", "query"],
  "text_queries": ["query", "query"],
  "table_queries": ["query", "query"],
  "table_intent": "required|supporting|none",
  "match_concepts": [{
    "canonical": "string",
    "variants": [],
    "kind": "metric|entity|dimension|topic"
  }],
  "metrics": [],
  "years": [],
  "entities": [],
  "units": [],
  "column_terms": []
}
Every query list must contain 2 or 3 distinct strings. Return JSON only."""


class RetrievalScope(str, Enum):
    NORMAL = "normal"
    BROAD = "broad"


class TableIntent(str, Enum):
    REQUIRED = "required"
    SUPPORTING = "supporting"
    NONE = "none"


class ConceptKind(str, Enum):
    METRIC = "metric"
    ENTITY = "entity"
    DIMENSION = "dimension"
    TOPIC = "topic"


class MatchConcept(BaseModel):
    canonical: str = Field(min_length=1, max_length=120)
    variants: list[str] = Field(default_factory=list, max_length=3)
    kind: ConceptKind

    @field_validator("canonical", mode="before")
    @classmethod
    def clean_canonical(cls, value: Any) -> str:
        return " ".join(str(value or "").split()).strip(" .,:;")

    @field_validator("variants", mode="before")
    @classmethod
    def clean_variants(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("concept variants must be returned as a list")
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            variant = " ".join(str(item or "").split()).strip(" .,:;")
            normalized = variant.casefold()
            if variant and normalized not in seen:
                seen.add(normalized)
                cleaned.append(variant)
        return cleaned


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
    table_intent: TableIntent = Field(
        description=(
            "required for structured exact comparisons, supporting when tables "
            "materially help, and none for purely narrative or isolated facts"
        )
    )
    match_concepts: list[MatchConcept] = Field(default_factory=list, max_length=12)
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


_EXPLICIT_PERIOD_RE = re.compile(r"\b(?:FY\s*)?(?:19|20)\d{2}\b", re.IGNORECASE)
_TABLE_REQUIRED_RE = re.compile(
    r"\b(?:compare|trend|correlat(?:e|ion)|calculate|compute|rank|"
    r"year[- ]over[- ]year|yoy)\b",
    re.IGNORECASE,
)
_METRIC_SIGNAL_RE = re.compile(
    r"\b(?:"
    r"program\s+services"
    r"|general\s+and\s+administration"
    r"|fundraising"
    r"|research\s+and\s+development(?:\s+(?:expense|expenditure)s?)?"
    r"|r\s*&\s*d(?:\s+(?:expense|expenditure)s?)?"
    r"|net\s+cash\s+(?:provided\s+by|used\s+in)\s+operating\s+activities"
    r"|(?:total|net|gross|operating|current|noncurrent|non-current)?\s*"
    r"(?:revenues?|sales|expenses?|costs?|assets?|liabilit(?:y|ies)|"
    r"income|earnings|profits?|loss(?:es)?|margin|cash\s+flows?|"
    r"equity|debt|headcount|employees?|rate|ratio|returns?|"
    r"contributions?|grants?|support)"
    r")\b",
    re.IGNORECASE,
)


def _unique_signals(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        signal = " ".join(value.split()).strip(" .,:;")
        canonical = signal.casefold()
        canonical = re.sub(
            r"\b(revenue|expense|asset|cost|profit|return|grant|"
            r"contribution|employee)s\b",
            r"\1",
            canonical,
        )
        canonical = re.sub(r"\bliabilities\b", "liability", canonical)
        if signal and canonical not in seen:
            seen.add(canonical)
            output.append(signal)
    return output


def _explicit_metric_signals(query: str) -> list[str]:
    return _unique_signals(
        [match.group(0) for match in _METRIC_SIGNAL_RE.finditer(query)]
    )


def _enrich_explicit_signals(
    parsed: GeneratedRetrievalQueries,
    query: str,
) -> GeneratedRetrievalQueries:
    """Restore explicit retrieval signals that a valid LLM response omitted."""

    metrics = _unique_signals([*parsed.metrics, *_explicit_metric_signals(query)])
    years = _unique_signals(
        [
            *parsed.years,
            *(match.group(0) for match in _EXPLICIT_PERIOD_RE.finditer(query)),
        ]
    )
    concepts = list(parsed.match_concepts)
    covered = {
        re.sub(
            r"\b(revenue|expense|asset|cost|profit|return|grant|"
            r"contribution|employee)s\b",
            r"\1",
            value.casefold(),
        )
        for concept in concepts
        for value in (concept.canonical, *concept.variants)
    }
    for metric in metrics:
        canonical_metric = re.sub(
            r"\b(revenue|expense|asset|cost|profit|return|grant|"
            r"contribution|employee)s\b",
            r"\1",
            metric.casefold(),
        )
        if canonical_metric not in covered:
            concepts.append(
                MatchConcept(canonical=metric, kind=ConceptKind.METRIC)
            )
            covered.add(canonical_metric)
    table_intent = parsed.table_intent
    if table_intent != TableIntent.REQUIRED and _TABLE_REQUIRED_RE.search(query):
        table_intent = TableIntent.REQUIRED
    return parsed.model_copy(
        update={
            "metrics": metrics[:10],
            "years": years[:10],
            "match_concepts": concepts[:12],
            "table_intent": table_intent,
        }
    )


@lru_cache(maxsize=1)
def get_query_generation_llm() -> AsyncQueryGenerator:
    """Return the small structured-output model used by query generation."""

    llm = ChatOpenAI(
        model=os.getenv(
            "DATA_ANALYSIS_QUERY_GENERATION_MODEL",
            "google/gemini-2.5-flash-lite",
        ),
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=0,
        max_retries=1,
        max_tokens=int(os.getenv("DATA_ANALYSIS_QUERY_GENERATION_MAX_TOKENS", "1600")),
        timeout=float(os.getenv("DATA_ANALYSIS_QUERY_GENERATION_TIMEOUT", "30")),
    )
    # JSON mode avoids provider-side schema grammar failures while Pydantic still
    # performs strict local validation and the node retains its bounded retry.
    return llm.with_structured_output(
        GeneratedRetrievalQueries,
        method="json_mode",
    )


def build_query_generation_node(
    query_generator: AsyncQueryGenerator | None = None,
) -> Any:
    """Build a one-call node with one malformed-output recovery attempt."""

    broad_cue = re.compile(
        r"\b(?:all|complete|comprehensive|in[- ]depth|overall)\b",
        re.IGNORECASE,
    )

    def resolved_scope(
        parsed: GeneratedRetrievalQueries,
        state: DataAnalysisRetrievalState,
        query: str,
    ) -> RetrievalScope:
        substantive_facets = sum(
            concept.kind in {ConceptKind.METRIC, ConceptKind.TOPIC}
            for concept in parsed.match_concepts
        )
        substantive_facets = max(substantive_facets, len(parsed.metrics))
        breadth_extent = max(len(parsed.years), len(parsed.entities))
        document_count = len(state.get("document_ids", []))
        if document_count >= 3:
            return RetrievalScope.BROAD
        if document_count >= 2 and broad_cue.search(query):
            return RetrievalScope.BROAD
        if substantive_facets >= 5 or breadth_extent >= 5:
            return RetrievalScope.BROAD
        if substantive_facets >= 3 and broad_cue.search(query):
            return RetrievalScope.BROAD
        return RetrievalScope.NORMAL

    def fallback_output(
        query: str,
        state: DataAnalysisRetrievalState,
    ) -> GeneratedRetrievalQueries:
        broad = bool(broad_cue.search(query)) or len(
            state.get("document_ids", [])
        ) >= 3
        return GeneratedRetrievalQueries(
            retrieval_scope=(
                RetrievalScope.BROAD if broad else RetrievalScope.NORMAL
            ),
            table_intent=TableIntent.SUPPORTING,
            shared_queries=[f"{query} key evidence", f"{query} overview"],
            text_queries=[f"{query} explanation", f"{query} discussion"],
            table_queries=[f"{query} table", f"{query} structured values"],
            match_concepts=[
                MatchConcept(canonical=query, kind=ConceptKind.TOPIC)
            ],
        )

    async def generate_queries(
        state: DataAnalysisRetrievalState,
    ) -> dict[str, Any]:
        query = " ".join(str(state.get("query") or "").split()).strip()
        if not query:
            raise ValueError("retrieval state query must not be empty")

        generator = query_generator or get_query_generation_llm()
        messages = [
            SystemMessage(
                content=(
                    QUERY_GENERATION_SYSTEM_PROMPT
                    + "\n\n"
                    + QUERY_GENERATION_JSON_INSTRUCTIONS
                )
            ),
            HumanMessage(
                content="User request:\n" + json.dumps(query, ensure_ascii=False)
            ),
        ]
        attempts = min(
            2,
            max(
                1,
                int(os.getenv("DATA_ANALYSIS_QUERY_GENERATION_ATTEMPTS", "2")),
            ),
        )
        parsed: GeneratedRetrievalQueries | None = None
        used_fallback = False
        attempts_used = 0
        for attempt in range(attempts):
            attempts_used = attempt + 1
            try:
                response = await generator.ainvoke(messages)
                parsed = (
                    response
                    if isinstance(response, GeneratedRetrievalQueries)
                    else GeneratedRetrievalQueries.model_validate(response)
                )
                break
            except (
                OutputParserException,
                ValidationError,
                ValueError,
                TypeError,
            ):
                if attempt + 1 == attempts:
                    parsed = fallback_output(query, state)
                    used_fallback = True
                    break
                messages = [
                    *messages,
                    SystemMessage(
                        content=(
                            "Return one complete object matching the required schema. "
                            "Do not truncate the JSON or add prose."
                        )
                    ),
                ]

        if parsed is None:  # pragma: no cover - guarded by the attempt loop
            raise RuntimeError("query generation returned no structured output")
        scope = resolved_scope(parsed, state, query)
        parsed = _enrich_explicit_signals(parsed, query)
        output = parsed.model_dump(mode="json")
        output["retrieval_scope"] = scope.value
        output["query_generation_attempts"] = attempts_used
        output["query_generation_fallback"] = used_fallback
        return output

    return generate_queries
