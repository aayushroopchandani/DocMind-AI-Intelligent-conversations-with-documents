from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Sequence

from ...models import (
    AnalysisIssue,
    AnalysisRequest,
    AnalysisRequirements,
    AugmentedDatasetReference,
    AugmentedEvidence,
    CompletionAttempt,
    CompletionAttemptOutcome,
    CompletionStage,
    CompletionStatus,
    CoverageStatus,
    DatasetAdditionOrigin,
    DerivedDatasetReference,
    DatasetProfiles,
    EvidenceAssessment,
    EvidenceFact,
    EvidencePackage,
    IssueCode,
    IssueSeverity,
    IssueStage,
    ReadinessDecision,
    RejectedEvidence,
    RetrievalResult,
    TextEvidenceReference,
    base_evidence_signature,
)
from ...repositories import EvidenceRepository, EvidenceRepositoryError
from ..assessment import EvidenceAssessmentRunner
from ..hydration import EvidenceHydrator
from ..profiling import DatasetProfilingRunner
from .candidates import CandidateRescueSelector, RescueSelection
from .repair import TargetedRepairRetriever
from .text import TextEvidenceCompletionService


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CompletionRunOutcome:
    artifact: AugmentedEvidence
    assessment: EvidenceAssessment
    warnings: tuple[AnalysisIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class _DatasetRescueOutcome:
    additions: tuple[AugmentedDatasetReference, ...] = ()
    profiles: DatasetProfiles | None = None
    warnings: tuple[AnalysisIssue, ...] = ()
    attempt: CompletionAttempt | None = None


def _artifact_status(requested: int, profiled: int) -> str:
    if requested == 0:
        return "empty"
    if requested == profiled:
        return "complete"
    return "failed" if profiled == 0 else "partial"


def _merge_profiles(
    base: DatasetProfiles,
    additions: Sequence[DatasetProfiles],
) -> DatasetProfiles:
    profiles = {item.dataset_id: item for item in base.profiles}
    failures = {item.dataset_id: item for item in base.failures}
    cache_hits = base.cache_hit_count
    generated = base.generated_count
    for artifact in additions:
        cache_hits += artifact.cache_hit_count
        generated += artifact.generated_count
        for item in artifact.profiles:
            failures.pop(item.dataset_id, None)
            profiles[item.dataset_id] = item
        for item in artifact.failures:
            if item.dataset_id not in profiles:
                failures[item.dataset_id] = item
    requested = len(profiles) + len(failures)
    return DatasetProfiles(
        profiler_version=base.profiler_version,
        status=_artifact_status(requested, len(profiles)),
        profiles=tuple(profiles.values()),
        failures=tuple(failures.values()),
        requested_count=requested,
        profiled_count=len(profiles),
        cache_hit_count=cache_hits,
        generated_count=generated,
    )


def _additional_profiles(
    *,
    profiler_version: str,
    values: Sequence[DatasetProfiles],
) -> DatasetProfiles | None:
    if not values:
        return None
    empty = DatasetProfiles(
        profiler_version=profiler_version,
        status="empty",
        requested_count=0,
        profiled_count=0,
        cache_hit_count=0,
        generated_count=0,
    )
    return _merge_profiles(empty, values)


def _merge_evidence(
    base: EvidencePackage,
    additions: Sequence[AugmentedDatasetReference],
) -> EvidencePackage:
    datasets = {item.dataset_id: item for item in base.datasets}
    for addition in additions:
        datasets[addition.dataset.dataset_id] = addition.dataset
    hydrated_table_ids = {value.table_id for value in datasets.values()}
    unresolved = {
        (item.table_id, item.document_id): item
        for item in base.unresolved_tables
        if item.table_id not in hydrated_table_ids
    }
    requested = len(datasets) + len(unresolved)
    hydrated = len(datasets)
    status = (
        "empty"
        if requested == 0
        else "complete"
        if requested == hydrated
        else "failed"
        if hydrated == 0
        else "partial"
    )
    return EvidencePackage(
        run_id=base.run_id,
        status=status,
        datasets=tuple(datasets.values()),
        unresolved_tables=tuple(unresolved.values()),
        retrieved_table_count=requested,
        hydrated_table_count=hydrated,
        created_at=base.created_at,
    )


def _merge_retrieval_text(
    base: RetrievalResult,
    additions: Sequence[TextEvidenceReference],
) -> RetrievalResult:
    chunks = {item.chunk_id: item for item in base.text_evidence}
    for item in additions:
        chunks.setdefault(item.chunk_id, item)
    return base.model_copy(update={"text_evidence": tuple(chunks.values())})


def _merge_facts(
    existing: Sequence[EvidenceFact],
    additions: Sequence[EvidenceFact],
) -> tuple[EvidenceFact, ...]:
    values = {item.fact_id: item for item in existing}
    for item in additions:
        values[item.fact_id] = item
    return tuple(values.values())[:30]


def _remaining_required_ids(
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


def _is_terminal(decision: ReadinessDecision) -> bool:
    return decision in {
        ReadinessDecision.READY,
        ReadinessDecision.NEEDS_CLARIFICATION,
        ReadinessDecision.UNANSWERABLE,
    }


def _maximum_repair_attempts() -> int:
    try:
        value = int(os.getenv("DATA_ANALYSIS_REPAIR_ATTEMPTS", "2"))
    except ValueError:
        value = 2
    return min(2, max(1, value))


_UNANSWERABLE_BLOCKING_ISSUES = frozenset(
    {
        IssueCode.CANDIDATE_RESCUE_FAILED,
        IssueCode.TEXT_EXTRACTION_FAILED,
        IssueCode.TARGETED_RETRIEVAL_FAILED,
        IssueCode.PROFILE_DATA_LOAD_FAILED,
        IssueCode.DATASET_NOT_AVAILABLE,
        IssueCode.DATASET_PROFILE_FAILED,
    }
)


class EvidenceCompletionRunner:
    """Bounded Phase 6 cascade with immutable base evidence and clear lineage."""

    def __init__(
        self,
        *,
        evidence_repository: EvidenceRepository,
        profiling_runner: DatasetProfilingRunner,
        assessment_runner: EvidenceAssessmentRunner,
        text_service: TextEvidenceCompletionService,
        repair_retriever: TargetedRepairRetriever,
        hydrator: EvidenceHydrator | None = None,
        candidate_selector: CandidateRescueSelector | None = None,
    ) -> None:
        self._evidence_repository = evidence_repository
        self._profiling_runner = profiling_runner
        self._assessment_runner = assessment_runner
        self._text_service = text_service
        self._repair_retriever = repair_retriever
        self._hydrator = hydrator or EvidenceHydrator()
        self._candidate_selector = candidate_selector or CandidateRescueSelector()

    async def run(
        self,
        *,
        run_id: str,
        request: AnalysisRequest,
        requirements: AnalysisRequirements,
        retrieval: RetrievalResult,
        evidence: EvidencePackage,
        profiles: DatasetProfiles,
        assessment: EvidenceAssessment,
    ) -> CompletionRunOutcome:
        initial_signature = base_evidence_signature(
            dataset_versions=tuple(
                (item.dataset_id, item.source_version)
                for item in evidence.datasets
            ),
            text_chunks=tuple(
                (
                    f"{item.document_id}:{item.chunk_id}",
                    item.content_hash
                    or hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
                )
                for item in retrieval.text_evidence
            ),
        )
        if _is_terminal(assessment.decision):
            return CompletionRunOutcome(
                artifact=AugmentedEvidence(
                    run_id=run_id,
                    base_evidence_signature=initial_signature,
                    status=(
                        CompletionStatus.READY
                        if assessment.decision == ReadinessDecision.READY
                        else CompletionStatus.SKIPPED
                    ),
                    base_dataset_ids=tuple(
                        item.dataset_id for item in evidence.datasets
                    ),
                    base_text_chunk_ids=tuple(
                        item.chunk_id for item in retrieval.text_evidence
                    ),
                    remaining_requirement_ids=_remaining_required_ids(
                        requirements,
                        assessment,
                    ),
                    final_decision=assessment.decision.value,
                ),
                assessment=assessment,
            )

        additions: list[AugmentedDatasetReference] = []
        profile_additions: list[DatasetProfiles] = []
        facts: tuple[EvidenceFact, ...] = ()
        derived: dict[str, DerivedDatasetReference] = {}
        attempts: list[CompletionAttempt] = []
        rejected: list[RejectedEvidence] = []
        warnings: list[AnalysisIssue] = []
        used_table_ids = {item.table_id for item in evidence.datasets}
        working_evidence = evidence
        working_profiles = profiles
        working_retrieval = retrieval
        working_assessment = assessment

        # 1. Rescue unused candidates retained before global fusion.
        selection = self._candidate_selector.select(
            requirements=requirements,
            assessment=working_assessment,
            candidates=retrieval.table_candidates,
            used_table_ids=used_table_ids,
        )
        used_table_ids.update(item.candidate.table_id for item in selection)
        rescued = await self._rescue_datasets(
            run_id=run_id,
            request=request,
            selections=selection,
            origin=DatasetAdditionOrigin.CANDIDATE_RESCUE,
            stage=CompletionStage.CANDIDATE_RESCUE,
            attempt_id="candidate_rescue_1",
        )
        warnings.extend(rescued.warnings)
        if rescued.attempt:
            attempts.append(rescued.attempt)
        if rescued.additions:
            additions.extend(rescued.additions)
            working_evidence = _merge_evidence(evidence, additions)
            if rescued.profiles:
                profile_additions.append(rescued.profiles)
                working_profiles = _merge_profiles(profiles, profile_additions)
            working_assessment, assessment_warnings = await self._reassess(
                request=request,
                requirements=requirements,
                retrieval=working_retrieval,
                evidence=working_evidence,
                profiles=working_profiles,
                facts=facts,
            )
            warnings.extend(assessment_warnings)

        # 2. Extract explicit values from text already retrieved by Phase 2.
        if not _is_terminal(working_assessment.decision):
            text_outcome = await self._text_service.run(
                user_id=request.user_id,
                requirements=requirements,
                assessment=working_assessment,
                chunks=working_retrieval.text_evidence,
                stage=CompletionStage.EXISTING_TEXT_EXTRACTION,
            )
            attempts.extend(text_outcome.attempts)
            rejected.extend(text_outcome.rejected)
            warnings.extend(text_outcome.warnings)
            facts = _merge_facts(facts, text_outcome.facts)
            derived.update(
                {
                    item.derived_dataset_id: item
                    for item in text_outcome.derived_datasets
                }
            )
            if text_outcome.facts:
                working_assessment, assessment_warnings = await self._reassess(
                    request=request,
                    requirements=requirements,
                    retrieval=working_retrieval,
                    evidence=working_evidence,
                    profiles=working_profiles,
                    facts=facts,
                )
                warnings.extend(assessment_warnings)

        # 3. Search only for requirements that remain incomplete.
        attempted_queries: set[str] = set()
        successful_repairs = 0
        for repair_number in range(1, _maximum_repair_attempts() + 1):
            if _is_terminal(working_assessment.decision):
                break
            try:
                repair = await self._repair_retriever.retrieve(
                    request=request,
                    requirements=requirements,
                    assessment=working_assessment,
                    attempted_queries=attempted_queries,
                    attempt=repair_number,
                )
            except Exception:
                logger.exception("Targeted evidence retrieval failed")
                warnings.append(
                    AnalysisIssue(
                        code=IssueCode.TARGETED_RETRIEVAL_FAILED,
                        severity=IssueSeverity.WARNING,
                        stage=IssueStage.COMPLETION,
                        message=(
                            f"Targeted retrieval attempt {repair_number} failed."
                        ),
                        retryable=True,
                    )
                )
                attempts.append(
                    CompletionAttempt(
                        attempt_id=f"targeted_retrieval_{repair_number}",
                        stage=CompletionStage.TARGETED_RETRIEVAL,
                        outcome=CompletionAttemptOutcome.FAILED,
                        requirement_ids=_remaining_required_ids(
                            requirements,
                            working_assessment,
                        ),
                        reason="The hybrid retrieval request failed.",
                    )
                )
                continue
            if not repair.queries:
                break
            successful_repairs += 1
            new_candidates = tuple(
                item
                for item in repair.table_candidates
                if item.table_id not in used_table_ids
            )
            selected = self._candidate_selector.select(
                requirements=requirements,
                assessment=working_assessment,
                candidates=new_candidates,
                used_table_ids=used_table_ids,
            )[: max(0, 16 - len(additions))]
            used_table_ids.update(item.candidate.table_id for item in selected)
            repaired = await self._rescue_datasets(
                run_id=run_id,
                request=request,
                selections=selected,
                origin=DatasetAdditionOrigin.RETRIEVAL_REPAIR,
                stage=CompletionStage.TARGETED_RETRIEVAL,
                attempt_id=f"targeted_retrieval_{repair_number}",
                queries=repair.queries,
                discovered_table_count=len(new_candidates),
                cache_hit=repair.cache_hit,
            )
            warnings.extend(repaired.warnings)
            if repaired.attempt:
                attempts.append(repaired.attempt)
            if repaired.additions:
                additions.extend(repaired.additions)
                working_evidence = _merge_evidence(evidence, additions)
                if repaired.profiles:
                    profile_additions.append(repaired.profiles)
                    working_profiles = _merge_profiles(profiles, profile_additions)

            known_chunk_ids = {
                chunk.chunk_id for chunk in working_retrieval.text_evidence
            }
            new_text = tuple(
                item
                for item in repair.text_evidence
                if item.chunk_id not in known_chunk_ids
            )
            working_retrieval = _merge_retrieval_text(
                working_retrieval,
                new_text,
            )
            if repaired.additions:
                working_assessment, assessment_warnings = await self._reassess(
                    request=request,
                    requirements=requirements,
                    retrieval=working_retrieval,
                    evidence=working_evidence,
                    profiles=working_profiles,
                    facts=facts,
                )
                warnings.extend(assessment_warnings)
            if not _is_terminal(working_assessment.decision) and new_text:
                text_outcome = await self._text_service.run(
                    user_id=request.user_id,
                    requirements=requirements,
                    assessment=working_assessment,
                    chunks=new_text,
                    stage=CompletionStage.REPAIR_TEXT_EXTRACTION,
                )
                attempts.extend(text_outcome.attempts)
                rejected.extend(text_outcome.rejected)
                warnings.extend(text_outcome.warnings)
                facts = _merge_facts(facts, text_outcome.facts)
                derived.update(
                    {
                        item.derived_dataset_id: item
                        for item in text_outcome.derived_datasets
                    }
                )
                if text_outcome.facts:
                    working_assessment, assessment_warnings = await self._reassess(
                        request=request,
                        requirements=requirements,
                        retrieval=working_retrieval,
                        evidence=working_evidence,
                        profiles=working_profiles,
                        facts=facts,
                    )
                    warnings.extend(assessment_warnings)

        remaining = _remaining_required_ids(requirements, working_assessment)
        if (
            remaining
            and successful_repairs
            and not any(
                item.code in _UNANSWERABLE_BLOCKING_ISSUES
                for item in warnings
            )
            and working_assessment.decision
            not in {
                ReadinessDecision.NEEDS_CLARIFICATION,
                ReadinessDecision.UNANSWERABLE,
            }
        ):
            working_assessment = working_assessment.model_copy(
                update={"decision": ReadinessDecision.UNANSWERABLE}
            )
            warnings.append(
                AnalysisIssue(
                    code=IssueCode.EVIDENCE_COMPLETION_EXHAUSTED,
                    severity=IssueSeverity.WARNING,
                    stage=IssueStage.COMPLETION,
                    message=(
                        "Bounded evidence completion finished without complete "
                        "support for every required item."
                    ),
                )
            )
        status = (
            CompletionStatus.READY
            if working_assessment.decision == ReadinessDecision.READY
            else CompletionStatus.EXHAUSTED
            if working_assessment.decision == ReadinessDecision.UNANSWERABLE
            else CompletionStatus.PARTIAL
        )
        artifact = AugmentedEvidence(
            run_id=run_id,
            base_evidence_signature=initial_signature,
            status=status,
            base_dataset_ids=tuple(
                item.dataset_id for item in evidence.datasets
            ),
            base_text_chunk_ids=tuple(
                item.chunk_id for item in retrieval.text_evidence
            ),
            added_datasets=tuple(additions)[:16],
            additional_profiles=_additional_profiles(
                profiler_version=profiles.profiler_version,
                values=profile_additions,
            ),
            facts=facts,
            derived_datasets=tuple(derived.values())[:10],
            attempts=tuple(attempts)[:16],
            rejected_evidence=tuple(rejected)[:30],
            remaining_requirement_ids=remaining,
            final_decision=working_assessment.decision.value,
        )
        return CompletionRunOutcome(
            artifact=artifact,
            assessment=working_assessment,
            warnings=tuple(warnings),
        )

    async def _rescue_datasets(
        self,
        *,
        run_id: str,
        request: AnalysisRequest,
        selections: tuple[RescueSelection, ...],
        origin: DatasetAdditionOrigin,
        stage: CompletionStage,
        attempt_id: str,
        queries: tuple[str, ...] = (),
        discovered_table_count: int | None = None,
        cache_hit: bool = False,
    ) -> _DatasetRescueOutcome:
        requirement_ids = tuple(
            dict.fromkeys(
                requirement_id
                for selection in selections
                for requirement_id in selection.requirement_ids
            )
        )
        if not selections:
            return _DatasetRescueOutcome(
                attempt=CompletionAttempt(
                    attempt_id=attempt_id,
                    stage=stage,
                    outcome=CompletionAttemptOutcome.NO_MATCH,
                    requirement_ids=requirement_ids,
                    queries=queries,
                    discovered_table_count=discovered_table_count or 0,
                    cache_hit=cache_hit,
                    reason="No unused table candidate passed the rescue gate.",
                )
            )
        references = tuple(
            item.candidate.as_retrieved_reference() for item in selections
        )
        try:
            sources = await self._evidence_repository.load_sources(
                user_id=request.user_id,
                document_ids=request.document_ids,
                table_ids=tuple(item.table_id for item in references),
            )
        except EvidenceRepositoryError:
            logger.exception("Candidate evidence hydration load failed")
            warning = AnalysisIssue(
                code=IssueCode.CANDIDATE_RESCUE_FAILED,
                severity=IssueSeverity.WARNING,
                stage=IssueStage.COMPLETION,
                message="Candidate tables could not be loaded for rescue.",
                retryable=True,
            )
            return _DatasetRescueOutcome(
                warnings=(warning,),
                attempt=CompletionAttempt(
                    attempt_id=attempt_id,
                    stage=stage,
                    outcome=CompletionAttemptOutcome.FAILED,
                    requirement_ids=requirement_ids,
                    queries=queries,
                    discovered_table_count=(
                        discovered_table_count
                        if discovered_table_count is not None
                        else len(selections)
                    ),
                    cache_hit=cache_hit,
                    reason="Authoritative candidate tables could not be loaded.",
                ),
            )
        hydration = self._hydrator.hydrate(
            run_id=run_id,
            user_id=request.user_id,
            document_ids=request.document_ids,
            references=references,
            sources=sources,
        )
        if not hydration.package.datasets:
            return _DatasetRescueOutcome(
                warnings=hydration.warnings,
                attempt=CompletionAttempt(
                    attempt_id=attempt_id,
                    stage=stage,
                    outcome=CompletionAttemptOutcome.NO_MATCH,
                    requirement_ids=requirement_ids,
                    queries=queries,
                    discovered_table_count=(
                        discovered_table_count
                        if discovered_table_count is not None
                        else len(selections)
                    ),
                    cache_hit=cache_hit,
                    reason="Selected candidates did not hydrate successfully.",
                ),
            )
        profiling = await self._profiling_runner.run(
            user_id=request.user_id,
            document_ids=request.document_ids,
            evidence=hydration.package,
        )
        requirement_ids_by_table = {
            item.candidate.table_id: item.requirement_ids for item in selections
        }
        additions = tuple(
            AugmentedDatasetReference(
                origin=origin,
                requirement_ids=requirement_ids_by_table[dataset.table_id],
                dataset=dataset,
            )
            for dataset in hydration.package.datasets
            if requirement_ids_by_table.get(dataset.table_id)
        )
        downgraded_errors = tuple(
            item.model_copy(
                update={
                    "severity": IssueSeverity.WARNING,
                    "stage": IssueStage.COMPLETION,
                }
            )
            for item in profiling.errors
        )
        warnings = (
            *hydration.warnings,
            *profiling.warnings,
            *downgraded_errors,
        )
        return _DatasetRescueOutcome(
            additions=additions,
            profiles=profiling.artifact,
            warnings=warnings,
            attempt=CompletionAttempt(
                attempt_id=attempt_id,
                stage=stage,
                outcome=(
                    CompletionAttemptOutcome.EVIDENCE_ADDED
                    if additions
                    else CompletionAttemptOutcome.NO_MATCH
                ),
                requirement_ids=requirement_ids,
                document_ids=tuple(
                    dict.fromkeys(
                        item.candidate.document_id for item in selections
                    )
                ),
                queries=queries,
                discovered_table_count=(
                    discovered_table_count
                    if discovered_table_count is not None
                    else len(selections)
                ),
                hydrated_table_count=len(additions),
                cache_hit=cache_hit,
                reason=(
                    "Hydrated and profiled requirement-specific candidates."
                    if additions
                    else "Candidates did not produce usable evidence references."
                ),
            ),
        )

    async def _reassess(
        self,
        *,
        request: AnalysisRequest,
        requirements: AnalysisRequirements,
        retrieval: RetrievalResult,
        evidence: EvidencePackage,
        profiles: DatasetProfiles,
        facts: tuple[EvidenceFact, ...],
    ) -> tuple[EvidenceAssessment, tuple[AnalysisIssue, ...]]:
        outcome = await self._assessment_runner.run(
            request=request,
            requirements=requirements,
            retrieval=retrieval,
            evidence=evidence,
            profiles=profiles,
            facts=facts,
        )
        return outcome.artifact, outcome.warnings
