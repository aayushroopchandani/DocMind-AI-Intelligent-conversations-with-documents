from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from scripts.data_analysis_agent.retrieval.state import DataAnalysisRetrievalState
from scripts.data_analysis_agent.retrieval.table_retrieval import (
    AsyncTableRetriever,
    QdrantTableRetriever,
)
from scripts.data_analysis_agent.retrieval.utils.embedding_cache import (
    SingleFlightEmbeddingCache,
)
from scripts.data_analysis_agent.retrieval.utils.hybrid_search import (
    HybridQdrantSearcher,
)
from scripts.data_analysis_agent.retrieval.text_retrieval import (
    AsyncTextRetriever,
    QdrantTextRetriever,
)

from ...models import (
    AnalysisRequest,
    AnalysisRequirements,
    CoverageStatus,
    EvidenceAssessment,
    RequirementKind,
    RetrievalResult,
    TableCandidateReference,
    TextEvidenceReference,
)


@dataclass(frozen=True, slots=True)
class TargetedRepairResult:
    queries: tuple[str, ...]
    document_ids: tuple[str, ...]
    table_candidates: tuple[TableCandidateReference, ...]
    text_evidence: tuple[TextEvidenceReference, ...]


class TargetedRepairRetriever(Protocol):
    async def retrieve(
        self,
        *,
        request: AnalysisRequest,
        requirements: AnalysisRequirements,
        assessment: EvidenceAssessment,
        attempted_queries: set[str],
        attempt: int,
    ) -> TargetedRepairResult: ...


def build_repair_queries(
    *,
    requirements: AnalysisRequirements,
    assessment: EvidenceAssessment,
    attempted_queries: set[str],
    attempt: int,
    max_queries: int = 4,
) -> tuple[str, ...]:
    """Generate focused, deterministic queries only for missing requirements."""

    incomplete_ids = {
        item.requirement_id
        for item in assessment.coverage
        if item.status != CoverageStatus.SUPPORTED
    }
    incomplete = tuple(
        item
        for item in requirements.requirements
        if item.required and item.requirement_id in incomplete_ids
    )
    substantive = tuple(
        item
        for item in incomplete
        if item.kind
        in {
            RequirementKind.METRIC,
            RequirementKind.DIMENSION,
            RequirementKind.FILTER,
            RequirementKind.TOPIC,
        }
    )
    if not substantive:
        substantive = tuple(
            item
            for item in requirements.requirements
            if item.required
            and item.kind
            in {
                RequirementKind.METRIC,
                RequirementKind.DIMENSION,
                RequirementKind.FILTER,
                RequirementKind.TOPIC,
            }
        )
    periods = tuple(
        item.name
        for item in requirements.requirements
        if item.required and item.kind == RequirementKind.PERIOD
    )
    entities = tuple(
        item.name
        for item in requirements.requirements
        if item.required and item.kind == RequirementKind.ENTITY
    )
    units = tuple(
        item.name
        for item in requirements.requirements
        if item.required and item.kind == RequirementKind.UNIT
    )
    financial_units = " ".join(
        (*units, *(item.unit or "" for item in substantive))
    ).casefold()
    second_attempt_terms = (
        ("year ended", "table", "explicit numeric value")
        if any(
            marker in financial_units
            for marker in (
                "usd",
                "eur",
                "gbp",
                "inr",
                "dollar",
                "$",
                "€",
                "£",
                "₹",
            )
        )
        else ("reported values", "data table", "explicit numeric value")
    )
    queries: list[str] = []
    for item in substantive:
        metric = (
            item.aliases[0]
            if attempt > 1 and item.aliases
            else item.name
        )
        parts = (
            *entities,
            *item.entity_names,
            metric,
            *periods,
            *units,
            *((item.unit,) if item.unit else ()),
            *(
                second_attempt_terms
                if attempt > 1
                else ("table",)
            ),
        )
        query = " ".join(dict.fromkeys(part.strip() for part in parts if part))
        normalized = " ".join(query.casefold().split())
        if query and normalized not in attempted_queries:
            attempted_queries.add(normalized)
            queries.append(query)
        if len(queries) >= max_queries:
            break
    return tuple(queries)


class QdrantTargetedRepairRetriever:
    """Run the existing hybrid retrievers without rerunning broad query generation."""

    def __init__(
        self,
        *,
        text_retriever: AsyncTextRetriever | None = None,
        table_retriever: AsyncTableRetriever | None = None,
    ) -> None:
        if text_retriever is None and table_retriever is None:
            searcher = HybridQdrantSearcher(
                embeddings=SingleFlightEmbeddingCache()
            )
            text_retriever = QdrantTextRetriever(searcher)
            table_retriever = QdrantTableRetriever(searcher)
        self._text_retriever = text_retriever or QdrantTextRetriever()
        self._table_retriever = table_retriever or QdrantTableRetriever()

    async def retrieve(
        self,
        *,
        request: AnalysisRequest,
        requirements: AnalysisRequirements,
        assessment: EvidenceAssessment,
        attempted_queries: set[str],
        attempt: int,
    ) -> TargetedRepairResult:
        queries = build_repair_queries(
            requirements=requirements,
            assessment=assessment,
            attempted_queries=attempted_queries,
            attempt=attempt,
        )
        target_documents = tuple(
            item.document_id
            for item in assessment.document_coverage
            if item.required and item.status != CoverageStatus.SUPPORTED
        ) or request.document_ids
        if not queries:
            return TargetedRepairResult(
                queries=(),
                document_ids=tuple(target_documents),
                table_candidates=(),
                text_evidence=(),
            )
        state = DataAnalysisRetrievalState(
            user_id=request.user_id,
            chat_id=request.chat_id,
            query=queries[0],
            document_ids=list(target_documents),
            retrieval_scope="broad",
            table_intent="required",
            shared_queries=list(queries[1:]),
            text_queries=[],
            table_queries=[],
            metrics=[
                item.name
                for item in requirements.requirements
                if item.kind == RequirementKind.METRIC
            ],
            years=[
                item.name
                for item in requirements.requirements
                if item.kind == RequirementKind.PERIOD
            ],
            entities=[
                item.name
                for item in requirements.requirements
                if item.kind == RequirementKind.ENTITY
            ],
            units=[
                item.name
                for item in requirements.requirements
                if item.kind == RequirementKind.UNIT
            ],
            column_terms=[
                item.name
                for item in requirements.requirements
                if item.kind
                in {RequirementKind.METRIC, RequirementKind.DIMENSION}
            ],
            match_concepts=[],
            retrieved_text_chunks=[],
            retrieved_tables=[],
            final_text_chunks=[],
            final_tables=[],
        )
        raw_text, raw_tables = await asyncio.gather(
            self._text_retriever.retrieve(state),
            self._table_retriever.retrieve(state),
        )
        adapted = RetrievalResult.from_retrieval_state(
            {
                **state,
                "final_text_chunks": raw_text,
                "retrieved_tables": raw_tables,
            }
        )
        return TargetedRepairResult(
            queries=queries,
            document_ids=tuple(target_documents),
            table_candidates=adapted.table_candidates,
            text_evidence=adapted.text_evidence,
        )
