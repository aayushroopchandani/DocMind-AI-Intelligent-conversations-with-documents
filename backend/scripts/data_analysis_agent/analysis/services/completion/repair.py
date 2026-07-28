from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    REPAIR_RETRIEVAL_CACHE_VERSION,
    RepairRetrievalCacheEntry,
    RequirementKind,
    RetrievalResult,
    TableCandidateReference,
    TextEvidenceReference,
)
from ...repositories import (
    MongoRepairRetrievalCache,
    RepairRetrievalCache,
    RepairRetrievalCacheError,
)


logger = logging.getLogger(__name__)
_MAX_REPAIR_TEXT_EVIDENCE = 30


@dataclass(frozen=True, slots=True)
class TargetedRepairResult:
    queries: tuple[str, ...]
    document_ids: tuple[str, ...]
    table_candidates: tuple[TableCandidateReference, ...]
    text_evidence: tuple[TextEvidenceReference, ...]
    cache_hit: bool = False


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


def _repair_cache_key(
    *,
    queries: tuple[str, ...],
    document_ids: tuple[str, ...],
    requirements: AnalysisRequirements,
    assessment: EvidenceAssessment,
    attempt: int,
) -> str:
    incomplete = sorted(
        (
            item.requirement_id,
            item.status.value,
        )
        for item in assessment.coverage
        if item.status != CoverageStatus.SUPPORTED
    )
    payload = {
        "queries": [" ".join(value.casefold().split()) for value in queries],
        "document_ids": sorted(set(document_ids)),
        "requirements_version": requirements.requirements_version,
        "incomplete": incomplete,
        "attempt": attempt,
        "cache_version": REPAIR_RETRIEVAL_CACHE_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cache_ttl(*, has_evidence: bool) -> timedelta:
    variable = (
        "DATA_ANALYSIS_REPAIR_POSITIVE_CACHE_MINUTES"
        if has_evidence
        else "DATA_ANALYSIS_REPAIR_NEGATIVE_CACHE_MINUTES"
    )
    fallback = 360 if has_evidence else 15
    try:
        minutes = max(1, int(os.getenv(variable, str(fallback))))
    except ValueError:
        minutes = fallback
    return timedelta(minutes=minutes)


class QdrantTargetedRepairRetriever:
    """Run the existing hybrid retrievers without rerunning broad query generation."""

    def __init__(
        self,
        *,
        text_retriever: AsyncTextRetriever | None = None,
        table_retriever: AsyncTableRetriever | None = None,
        cache: RepairRetrievalCache | None = None,
    ) -> None:
        use_default_dependencies = (
            text_retriever is None and table_retriever is None
        )
        if text_retriever is None and table_retriever is None:
            searcher = HybridQdrantSearcher(
                embeddings=SingleFlightEmbeddingCache()
            )
            text_retriever = QdrantTextRetriever(searcher)
            table_retriever = QdrantTableRetriever(searcher)
        self._text_retriever = text_retriever or QdrantTextRetriever()
        self._table_retriever = table_retriever or QdrantTableRetriever()
        self._cache = (
            cache
            if cache is not None
            else MongoRepairRetrievalCache()
            if use_default_dependencies
            else None
        )

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
        cache_key = _repair_cache_key(
            queries=queries,
            document_ids=tuple(target_documents),
            requirements=requirements,
            assessment=assessment,
            attempt=attempt,
        )
        if self._cache is not None:
            try:
                cached = await self._cache.load(
                    user_id=request.user_id,
                    cache_key=cache_key,
                )
                if cached is not None:
                    return TargetedRepairResult(
                        queries=cached.queries,
                        document_ids=cached.document_ids,
                        table_candidates=cached.table_candidates,
                        text_evidence=cached.text_evidence,
                        cache_hit=True,
                    )
            except RepairRetrievalCacheError:
                logger.warning(
                    "Targeted-retrieval cache read failed; querying Qdrant",
                    exc_info=True,
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
        result = TargetedRepairResult(
            queries=queries,
            document_ids=tuple(target_documents),
            table_candidates=adapted.table_candidates,
            text_evidence=adapted.text_evidence[:_MAX_REPAIR_TEXT_EVIDENCE],
        )
        if self._cache is not None:
            has_evidence = bool(result.table_candidates or result.text_evidence)
            entry = RepairRetrievalCacheEntry(
                queries=result.queries,
                document_ids=result.document_ids,
                table_candidates=result.table_candidates,
                text_evidence=result.text_evidence,
                expires_at=(
                    datetime.now(timezone.utc)
                    + _cache_ttl(has_evidence=has_evidence)
                ),
            )
            try:
                await self._cache.save(
                    user_id=request.user_id,
                    cache_key=cache_key,
                    entry=entry,
                )
            except RepairRetrievalCacheError:
                logger.warning(
                    "Targeted-retrieval cache write failed",
                    exc_info=True,
                )
        return result
