from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ...models import (
    AnalysisIssue,
    AnalysisRequirements,
    CompletionAttempt,
    CompletionAttemptOutcome,
    CompletionStage,
    CoverageStatus,
    DerivedDatasetReference,
    EvidenceAssessment,
    EvidenceFact,
    IssueCode,
    IssueSeverity,
    IssueStage,
    RejectedEvidence,
    RequirementKind,
    TextEvidenceReference,
    TextExtractionCacheEntry,
    TextExtractionResponse,
    TEXT_EVIDENCE_EXTRACTOR_VERSION,
    TEXT_EVIDENCE_PROMPT_VERSION,
)
from ...repositories import (
    DerivedDatasetRepository,
    DerivedDatasetRepositoryError,
    TextExtractionCache,
    TextExtractionCacheError,
)
from ..assessment.rules import contains_phrase, lexical_score
from .derived import build_derived_dataset_writes
from .deterministic import extract_labeled_numeric_facts
from .extractor import StructuredTextEvidenceExtractor, TextExtractionTask
from .validation import (
    TextExtractionValidationResult,
    validate_text_extraction,
)


from scripts.data_analysis_agent.runtime.observability.logging import (
    get_analysis_logger,
)


logger = get_analysis_logger(__name__)
_NUMBER_RE = re.compile(r"(?:^|[^\w])[-+]?[$€£₹]?\s*\d[\d,.]*")
_DEFAULT_MAX_CHUNKS_PER_DOCUMENT = 4
_DEFAULT_MAX_CONCURRENCY = 3
_DEFAULT_MAX_CHARS_PER_DOCUMENT = 16_000


@dataclass(frozen=True, slots=True)
class TextCompletionOutcome:
    facts: tuple[EvidenceFact, ...] = ()
    derived_datasets: tuple[DerivedDatasetReference, ...] = ()
    attempts: tuple[CompletionAttempt, ...] = ()
    rejected: tuple[RejectedEvidence, ...] = ()
    warnings: tuple[AnalysisIssue, ...] = ()


def _positive_env(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return min(maximum, max(1, value))


def _chunk_score(
    chunk: TextEvidenceReference,
    requirement_terms: tuple[str, ...],
) -> float:
    if not _NUMBER_RE.search(chunk.text):
        return -1.0
    score = min(0.20, float(chunk.relevance_score or 0.0))
    for term in requirement_terms:
        if contains_phrase(chunk.text, term):
            score += 1.0
        else:
            score += 0.30 * lexical_score(term, chunk.text[:3000])
    return score


def _content_hash(chunk: TextEvidenceReference) -> str:
    return chunk.content_hash or hashlib.sha256(
        chunk.text.encode("utf-8")
    ).hexdigest()


def _bounded_chunk(
    chunk: TextEvidenceReference,
    *,
    terms: tuple[str, ...],
    maximum_chars: int,
) -> TextEvidenceReference:
    if len(chunk.text) <= maximum_chars:
        return chunk.model_copy(update={"content_hash": _content_hash(chunk)})
    folded = chunk.text.casefold()
    positions = [
        position
        for term in terms
        if term and (position := folded.find(term.casefold())) >= 0
    ]
    center = min(positions) if positions else 0
    start = max(0, center - maximum_chars // 3)
    start = min(start, len(chunk.text) - maximum_chars)
    return chunk.model_copy(
        update={
            "text": chunk.text[start : start + maximum_chars],
            "content_hash": _content_hash(chunk),
            "text_offset": chunk.text_offset + start,
        }
    )


def _incomplete_required_ids(
    requirements: AnalysisRequirements,
    assessment: EvidenceAssessment,
) -> tuple[str, ...]:
    required = {
        item.requirement_id
        for item in requirements.requirements
        if item.required
    }
    return tuple(
        item.requirement_id
        for item in assessment.coverage
        if item.requirement_id in required
        and item.status != CoverageStatus.SUPPORTED
    )


def build_text_extraction_tasks(
    *,
    requirements: AnalysisRequirements,
    assessment: EvidenceAssessment,
    chunks: tuple[TextEvidenceReference, ...],
    max_chunks_per_document: int = _DEFAULT_MAX_CHUNKS_PER_DOCUMENT,
    max_chars_per_document: int = _DEFAULT_MAX_CHARS_PER_DOCUMENT,
) -> tuple[TextExtractionTask, ...]:
    """Build small document-scoped prompts from already retrieved chunks."""

    incomplete_ids = _incomplete_required_ids(requirements, assessment)
    if not incomplete_ids:
        return ()
    items_by_id = {
        item.requirement_id: item for item in requirements.requirements
    }
    target_requirements = tuple(
        items_by_id[item_id]
        for item_id in incomplete_ids
        if item_id in items_by_id
    )
    extraction_targets = tuple(
        item
        for item in target_requirements
        if item.kind
        in {
            RequirementKind.METRIC,
            RequirementKind.DIMENSION,
            RequirementKind.FILTER,
            RequirementKind.TOPIC,
        }
    )
    if not extraction_targets:
        extraction_targets = tuple(
            item
            for item in requirements.requirements
            if item.required and item.kind == RequirementKind.METRIC
        )
    if not extraction_targets:
        return ()
    extraction_requirement_ids = tuple(
        dict.fromkeys(
            (
                *incomplete_ids,
                *(item.requirement_id for item in extraction_targets),
            )
        )
    )
    context_requirements = tuple(
        item
        for item in requirements.requirements
        if item.requirement_id in extraction_requirement_ids
        or (
            item.required
            and item.kind
            in {
                RequirementKind.ENTITY,
                RequirementKind.PERIOD,
                RequirementKind.UNIT,
            }
        )
    )

    incomplete_documents = {
        item.document_id
        for item in assessment.document_coverage
        if item.required and item.status != CoverageStatus.SUPPORTED
    }
    if not incomplete_documents:
        incomplete_documents = set(requirements.selected_document_ids)
    requirement_terms = tuple(
        dict.fromkeys(
            term
            for item in (*target_requirements, *extraction_targets)
            for term in (item.name, *item.aliases, *item.entity_names)
            if term
        )
    )
    by_document: dict[str, list[TextEvidenceReference]] = {}
    for chunk in chunks:
        if chunk.document_id in incomplete_documents:
            by_document.setdefault(chunk.document_id, []).append(chunk)

    tasks: list[TextExtractionTask] = []
    for document_id in requirements.selected_document_ids:
        candidates = by_document.get(document_id, [])
        ranked = sorted(
            (
                (_chunk_score(chunk, requirement_terms), chunk)
                for chunk in candidates
            ),
            key=lambda item: (
                -item[0],
                item[1].page_number or 0,
                item[1].chunk_id,
            ),
        )
        ranked_candidates = tuple(
            chunk
            for score, chunk in ranked[:max_chunks_per_document]
            if score >= 0
        )
        selected_values: list[TextEvidenceReference] = []
        remaining_chars = max_chars_per_document
        for chunk in ranked_candidates:
            if remaining_chars <= 0:
                break
            bounded = _bounded_chunk(
                chunk,
                terms=requirement_terms,
                maximum_chars=remaining_chars,
            )
            selected_values.append(bounded)
            remaining_chars -= len(bounded.text)
        selected = tuple(selected_values)
        if selected:
            tasks.append(
                TextExtractionTask(
                    document_id=document_id,
                    target_requirement_ids=extraction_requirement_ids,
                    requirements=context_requirements,
                    chunks=selected,
                )
            )
    return tuple(tasks)


def text_extraction_cache_key(
    task: TextExtractionTask,
    *,
    model: str,
) -> str:
    payload = {
        "document_id": task.document_id,
        "chunks": [
            (
                item.chunk_id,
                _content_hash(item),
                item.text_offset,
                hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
            )
            for item in task.chunks
        ],
        "requirements": [
            item.model_dump(mode="json")
            for item in task.requirements
            if item.requirement_id in task.target_requirement_ids
            or item.kind in {
                RequirementKind.ENTITY,
                RequirementKind.PERIOD,
                RequirementKind.UNIT,
            }
        ],
        "target_requirement_ids": list(task.target_requirement_ids),
        "extractor_version": TEXT_EVIDENCE_EXTRACTOR_VERSION,
        "prompt_version": TEXT_EVIDENCE_PROMPT_VERSION,
        "model": model,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class TextEvidenceCompletionService:
    """Cache, extract, validate, and persist text-derived evidence."""

    def __init__(
        self,
        *,
        cache: TextExtractionCache,
        derived_repository: DerivedDatasetRepository,
        extractor: StructuredTextEvidenceExtractor | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self._cache = cache
        self._derived_repository = derived_repository
        self._extractor = extractor or StructuredTextEvidenceExtractor()
        self._max_concurrency = max_concurrency or _positive_env(
            "DATA_ANALYSIS_TEXT_EVIDENCE_CONCURRENCY",
            _DEFAULT_MAX_CONCURRENCY,
            6,
        )

    async def run(
        self,
        *,
        user_id: str,
        requirements: AnalysisRequirements,
        assessment: EvidenceAssessment,
        chunks: tuple[TextEvidenceReference, ...],
        stage: CompletionStage,
    ) -> TextCompletionOutcome:
        tasks = build_text_extraction_tasks(
            requirements=requirements,
            assessment=assessment,
            chunks=chunks,
            max_chunks_per_document=_positive_env(
                "DATA_ANALYSIS_TEXT_EVIDENCE_CHUNKS_PER_DOCUMENT",
                _DEFAULT_MAX_CHUNKS_PER_DOCUMENT,
                8,
            ),
            max_chars_per_document=max(
                1000,
                _positive_env(
                    "DATA_ANALYSIS_TEXT_EVIDENCE_MAX_CHARS_PER_DOCUMENT",
                    _DEFAULT_MAX_CHARS_PER_DOCUMENT,
                    40_000,
                ),
            ),
        )
        if not tasks:
            return TextCompletionOutcome()
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def run_one(
            ordinal: int,
            task: TextExtractionTask,
        ) -> TextCompletionOutcome:
            async with semaphore:
                return await self._run_one(
                    ordinal=ordinal,
                    user_id=user_id,
                    task=task,
                    stage=stage,
                )

        outcomes = await asyncio.gather(
            *(run_one(index, task) for index, task in enumerate(tasks, start=1))
        )
        facts = {
            fact.fact_id: fact
            for outcome in outcomes
            for fact in outcome.facts
        }
        derived = {
            item.derived_dataset_id: item
            for outcome in outcomes
            for item in outcome.derived_datasets
        }
        return TextCompletionOutcome(
            facts=tuple(facts.values())[:30],
            derived_datasets=tuple(derived.values())[:10],
            attempts=tuple(
                attempt for outcome in outcomes for attempt in outcome.attempts
            ),
            rejected=tuple(
                item for outcome in outcomes for item in outcome.rejected
            )[:30],
            warnings=tuple(
                item for outcome in outcomes for item in outcome.warnings
            ),
        )

    async def _run_one(
        self,
        *,
        ordinal: int,
        user_id: str,
        task: TextExtractionTask,
        stage: CompletionStage,
    ) -> TextCompletionOutcome:
        cache_key = text_extraction_cache_key(task, model=self._extractor.model)
        attempt_id = f"{stage.value}_{ordinal}_{cache_key[:12]}"
        warnings: list[AnalysisIssue] = []
        try:
            cached = await self._cache.load(
                user_id=user_id,
                cache_key=cache_key,
            )
        except TextExtractionCacheError:
            logger.exception("Text evidence cache read failed")
            cached = None
            warnings.append(
                AnalysisIssue(
                    code=IssueCode.TEXT_EXTRACTION_CACHE_READ_FAILED,
                    severity=IssueSeverity.WARNING,
                    stage=IssueStage.COMPLETION,
                    message="Cached text evidence could not be read.",
                    retryable=True,
                    document_id=task.document_id,
                )
            )
        if cached is not None:
            return TextCompletionOutcome(
                facts=cached.facts,
                derived_datasets=cached.derived_datasets,
                rejected=cached.rejected_evidence,
                warnings=tuple(warnings),
                attempts=(
                    CompletionAttempt(
                        attempt_id=attempt_id,
                        stage=stage,
                        outcome=CompletionAttemptOutcome.CACHE_HIT,
                        requirement_ids=task.target_requirement_ids,
                        document_ids=(task.document_id,),
                        accepted_fact_count=len(cached.facts),
                        cache_hit=True,
                        reason=f"Reused cached {cached.status} extraction.",
                    ),
                ),
            )

        try:
            response, _attempt_count = await self._extractor.extract(task)
        except Exception:
            logger.exception("Structured text evidence extraction failed")
            warnings.append(
                AnalysisIssue(
                    code=IssueCode.TEXT_EXTRACTION_FAILED,
                    severity=IssueSeverity.WARNING,
                    stage=IssueStage.COMPLETION,
                    message=(
                        "The extraction model failed; explicit labeled values "
                        "were checked deterministically."
                    ),
                    retryable=True,
                    document_id=task.document_id,
                )
            )
            response = TextExtractionResponse(status="absent")

        validation = validate_text_extraction(
            response=response,
            requirements=task.requirements,
            chunks=task.chunks,
            model=self._extractor.model,
            stage=stage,
        )
        deterministic_validation = validate_text_extraction(
            response=extract_labeled_numeric_facts(task),
            requirements=task.requirements,
            chunks=task.chunks,
            model=self._extractor.model,
            stage=stage,
        )
        combined_facts = {
            fact.fact_id: fact
            for fact in (*validation.facts, *deterministic_validation.facts)
        }
        combined_rejected = tuple(
            {
                (
                    item.requirement_id,
                    item.document_id,
                    item.chunk_id,
                    item.reason,
                ): item
                for item in (
                    *validation.rejected,
                    *deterministic_validation.rejected,
                )
            }.values()
        )
        validation = TextExtractionValidationResult(
            facts=tuple(combined_facts.values())[:30],
            rejected=combined_rejected[:30],
        )
        derived: list[DerivedDatasetReference] = []
        for value in build_derived_dataset_writes(validation.facts):
            try:
                derived.append(
                    await self._derived_repository.save(
                        user_id=user_id,
                        value=value,
                    )
                )
            except DerivedDatasetRepositoryError:
                logger.exception("Derived dataset persistence failed")
                warnings.append(
                    AnalysisIssue(
                        code=IssueCode.DERIVED_DATASET_WRITE_FAILED,
                        severity=IssueSeverity.WARNING,
                        stage=IssueStage.COMPLETION,
                        message=(
                            "Validated facts remain available, but their reusable "
                            "derived dataset could not be cached."
                        ),
                        retryable=True,
                        document_id=task.document_id,
                    )
                )

        if validation.facts:
            status = "accepted"
            outcome = CompletionAttemptOutcome.EVIDENCE_ADDED
            reason = "Validated explicit numeric facts were extracted."
            ttl_days = _positive_env(
                "DATA_ANALYSIS_TEXT_EVIDENCE_SUCCESS_TTL_DAYS",
                30,
                365,
            )
        elif validation.rejected:
            status = "rejected"
            outcome = CompletionAttemptOutcome.NO_MATCH
            reason = "Candidate facts failed deterministic source validation."
            ttl_days = _positive_env(
                "DATA_ANALYSIS_TEXT_EVIDENCE_NEGATIVE_TTL_DAYS",
                1,
                30,
            )
        else:
            status = "absent"
            outcome = CompletionAttemptOutcome.NO_MATCH
            reason = "No explicit target evidence was found in selected chunks."
            ttl_days = _positive_env(
                "DATA_ANALYSIS_TEXT_EVIDENCE_NEGATIVE_TTL_DAYS",
                1,
                30,
            )
        entry = TextExtractionCacheEntry(
            status=status,
            facts=validation.facts,
            derived_datasets=tuple(derived),
            rejected_evidence=validation.rejected,
            expires_at=datetime.now(timezone.utc) + timedelta(days=ttl_days),
        )
        try:
            await self._cache.save(
                user_id=user_id,
                cache_key=cache_key,
                entry=entry,
            )
        except TextExtractionCacheError:
            logger.exception("Text evidence cache write failed")
            warnings.append(
                AnalysisIssue(
                    code=IssueCode.TEXT_EXTRACTION_CACHE_WRITE_FAILED,
                    severity=IssueSeverity.WARNING,
                    stage=IssueStage.COMPLETION,
                    message="Text extraction result could not be cached.",
                    retryable=True,
                    document_id=task.document_id,
                )
            )
        return TextCompletionOutcome(
            facts=validation.facts,
            derived_datasets=tuple(derived),
            rejected=validation.rejected,
            warnings=tuple(warnings),
            attempts=(
                CompletionAttempt(
                    attempt_id=attempt_id,
                    stage=stage,
                    outcome=outcome,
                    requirement_ids=task.target_requirement_ids,
                    document_ids=(task.document_id,),
                    accepted_fact_count=len(validation.facts),
                    reason=reason,
                ),
            ),
        )
