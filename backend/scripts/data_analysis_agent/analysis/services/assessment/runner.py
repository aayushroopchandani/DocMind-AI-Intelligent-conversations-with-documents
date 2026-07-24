from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Mapping

from ...models import (
    AnalysisIssue,
    AnalysisRequest,
    DatasetProfiles,
    EvidencePackage,
    IssueCode,
    IssueSeverity,
    IssueStage,
    RetrievalResult,
    SemanticRole,
)
from ...models.assessment import (
    AMBIGUITY_PROMPT_VERSION,
    EVIDENCE_ASSESSOR_VERSION,
    AssessmentDiagnostics,
    CoverageStatus,
    DocumentCoverage,
    EvidenceAssessment,
    EvidenceKind,
    MatchMethod,
    ReadinessDecision,
    RequirementCoverage,
    assessment_cache_key,
)
from ...models.requirements import (
    AnalysisRequirements,
    RequirementKind,
)
from ...repositories.assessment_cache import (
    AssessmentCache,
    AssessmentCacheError,
)
from ...repositories.assessment_metadata import (
    AssessmentMetadataRepository,
    AssessmentMetadataRepositoryError,
    TableAssessmentMetadata,
)
from .matcher import AmbiguityCandidate, DeterministicEvidenceMatcher
from .resolver import AmbiguityDecision, AmbiguityResolver
from .rules import contains_phrase, lexical_score, normalized_phrase


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AssessmentRunOutcome:
    artifact: EvidenceAssessment
    warnings: tuple[AnalysisIssue, ...] = ()


def _metadata_signatures(
    metadata: Mapping[str, TableAssessmentMetadata],
) -> tuple[tuple[str, str], ...]:
    output = []
    for table_id, item in metadata.items():
        digest = hashlib.sha256()
        digest.update(item.title.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(item.summary.encode("utf-8"))
        for keyword in item.keywords:
            digest.update(b"\x1e")
            digest.update(keyword.encode("utf-8"))
        output.append((table_id, digest.hexdigest()))
    return tuple(sorted(output))


def _apply_ambiguity_resolutions(
    *,
    requirements: AnalysisRequirements,
    coverage: tuple[RequirementCoverage, ...],
    candidates: tuple[AmbiguityCandidate, ...],
    resolutions: Mapping[str, object],
) -> tuple[tuple[RequirementCoverage, ...], int]:
    candidates_by_requirement: dict[str, list[AmbiguityCandidate]] = {}
    for candidate in candidates:
        candidates_by_requirement.setdefault(
            candidate.requirement.requirement_id,
            [],
        ).append(candidate)
    requirements_by_id = {
        item.requirement_id: item for item in requirements.requirements
    }
    output: list[RequirementCoverage] = []
    resolved_count = 0
    for item in coverage:
        item_candidates = candidates_by_requirement.get(item.requirement_id, [])
        if not item_candidates:
            output.append(item)
            continue
        matched = []
        explicit_no_match = 0
        ambiguous_left = 0
        for candidate in item_candidates:
            resolution = resolutions.get(candidate.pair_id)
            decision = getattr(resolution, "decision", None)
            confidence = float(getattr(resolution, "confidence", 0.0))
            if decision == AmbiguityDecision.MATCH and confidence >= 0.80:
                matched.append(
                    candidate.evidence.model_copy(
                        update={
                            "confidence": confidence,
                            "match_method": MatchMethod.LLM,
                        }
                    )
                )
                resolved_count += 1
            elif decision == AmbiguityDecision.NO_MATCH and confidence >= 0.70:
                explicit_no_match += 1
                resolved_count += 1
            else:
                ambiguous_left += 1

        requirement = requirements_by_id[item.requirement_id]
        required_documents = (
            set(requirements.selected_document_ids)
            if requirements.requires_all_selected_documents
            and requirement.kind
            in {
                RequirementKind.METRIC,
                RequirementKind.PERIOD,
                RequirementKind.DIMENSION,
                RequirementKind.UNIT,
            }
            else set()
        )
        matched_documents = {value.document_id for value in matched}
        missing_documents = required_documents - matched_documents
        if matched:
            combined_evidence = tuple(
                dict.fromkeys((*matched, *item.evidence))
            )[:12]
            output.append(
                RequirementCoverage(
                    requirement_id=item.requirement_id,
                    status=(
                        CoverageStatus.PARTIAL
                        if missing_documents
                        else CoverageStatus.SUPPORTED
                    ),
                    confidence=max(value.confidence for value in matched),
                    reason=(
                        "The ambiguity resolver confirmed a strict semantic match."
                        if not missing_documents
                        else (
                            "A semantic match was confirmed, but selected document "
                            "coverage remains incomplete."
                        )
                    ),
                    evidence=combined_evidence,
                    text_evidence_available=item.text_evidence_available,
                )
            )
        elif ambiguous_left:
            output.append(item)
        elif explicit_no_match and item.status == CoverageStatus.AMBIGUOUS:
            output.append(
                RequirementCoverage(
                    requirement_id=item.requirement_id,
                    status=CoverageStatus.MISSING,
                    confidence=0.0,
                    reason="Ambiguous candidates were rejected as non-equivalent.",
                    text_evidence_available=item.text_evidence_available,
                )
            )
        else:
            output.append(item)
    return tuple(output), resolved_count


def _document_coverage(
    *,
    requirements: AnalysisRequirements,
    evidence: EvidencePackage,
    profiles: DatasetProfiles,
    retrieval: RetrievalResult,
) -> tuple[DocumentCoverage, ...]:
    profile_by_dataset = {
        profile.dataset_id: profile for profile in profiles.profiles
    }
    datasets_by_document: dict[str, list[str]] = {
        document_id: [] for document_id in requirements.selected_document_ids
    }
    names_by_document: dict[str, str] = {}
    for dataset in evidence.datasets:
        names_by_document[dataset.document_id] = dataset.document_name
        profile = profile_by_dataset.get(dataset.dataset_id)
        if profile is not None and profile.suitable_for_analysis:
            datasets_by_document.setdefault(dataset.document_id, []).append(
                dataset.dataset_id
            )
    chunks_by_document: dict[str, list[str]] = {
        document_id: [] for document_id in requirements.selected_document_ids
    }
    for chunk in retrieval.text_evidence:
        names_by_document.setdefault(chunk.document_id, chunk.document_name)
        chunks_by_document.setdefault(chunk.document_id, []).append(chunk.chunk_id)

    explicit_entities = {
        entity
        for item in requirements.requirements
        if item.required
        for entity in (
            (item.name,)
            if item.kind == RequirementKind.ENTITY
            else item.entity_names
        )
    }
    explicitly_required_documents: set[str] = set()
    for document_id in requirements.selected_document_ids:
        document_text = names_by_document.get(document_id, "")
        chunk_text = "\n".join(
            chunk.text
            for chunk in retrieval.text_evidence
            if chunk.document_id == document_id
        )
        if any(
            contains_phrase(f"{document_text}\n{chunk_text}", entity)
            or lexical_score(entity, document_text) >= 0.76
            for entity in explicit_entities
        ):
            explicitly_required_documents.add(document_id)

    require_every_document = (
        len(requirements.selected_document_ids) == 1
        or requirements.requires_all_selected_documents
    )
    output: list[DocumentCoverage] = []
    for document_id in requirements.selected_document_ids:
        dataset_ids = tuple(dict.fromkeys(datasets_by_document.get(document_id, [])))
        chunk_ids = tuple(dict.fromkeys(chunks_by_document.get(document_id, [])))
        if requirements.table_evidence_required:
            status = (
                CoverageStatus.SUPPORTED
                if dataset_ids
                else CoverageStatus.PARTIAL
                if chunk_ids and requirements.text_evidence_acceptable
                else CoverageStatus.MISSING
            )
        else:
            status = (
                CoverageStatus.SUPPORTED
                if dataset_ids or chunk_ids
                else CoverageStatus.MISSING
            )
        output.append(
            DocumentCoverage(
                document_id=document_id,
                document_name=names_by_document.get(document_id, ""),
                required=(
                    require_every_document
                    or document_id in explicitly_required_documents
                ),
                status=status,
                dataset_ids=dataset_ids,
                text_chunk_ids=chunk_ids,
            )
        )
    return tuple(output)


def _join_is_supported(
    *,
    requirements: AnalysisRequirements,
    evidence: EvidencePackage,
    profiles: DatasetProfiles,
) -> bool:
    if not requirements.requires_join:
        return True
    selected_ids = {item.dataset_id for item in evidence.datasets}
    usable = [
        profile
        for profile in profiles.profiles
        if profile.dataset_id in selected_ids and profile.suitable_for_analysis
    ]
    if len(usable) < 2:
        return False
    label_sets = []
    has_time_axis = []
    for profile in usable:
        join_labels = {
            normalized_phrase(column.label)
            for column in profile.columns
            if column.semantic_role
            in {
                SemanticRole.DIMENSION,
                SemanticRole.CATEGORY,
                SemanticRole.IDENTIFIER,
                SemanticRole.TIME_PERIOD,
            }
            and normalized_phrase(column.label)
        }
        label_sets.append(join_labels)
        has_time_axis.append(
            any(
                column.semantic_role == SemanticRole.TIME_PERIOD
                for column in profile.columns
            )
            or profile.periods_in_headers
        )
    common_labels = set.intersection(*label_sets) if label_sets else set()
    return bool(common_labels) or all(has_time_axis)


def _readiness(
    *,
    requirements: AnalysisRequirements,
    coverage: tuple[RequirementCoverage, ...],
    document_coverage: tuple[DocumentCoverage, ...],
    evidence: EvidencePackage,
    profiles: DatasetProfiles,
) -> ReadinessDecision:
    if requirements.diagnostics.validation_conflicts:
        return ReadinessDecision.NEEDS_CLARIFICATION
    required_ids = {
        item.requirement_id
        for item in requirements.requirements
        if item.required
    }
    required_coverage = [
        item for item in coverage if item.requirement_id in required_ids
    ]
    if not required_coverage:
        return ReadinessDecision.NEEDS_CLARIFICATION
    if any(
        item.status in {CoverageStatus.CONFLICTING, CoverageStatus.AMBIGUOUS}
        for item in required_coverage
    ):
        return ReadinessDecision.NEEDS_CLARIFICATION
    incomplete_documents = [
        item
        for item in document_coverage
        if item.required and item.status != CoverageStatus.SUPPORTED
    ]
    incomplete = [
        item
        for item in required_coverage
        if item.status != CoverageStatus.SUPPORTED
    ]
    if incomplete or incomplete_documents:
        if any(item.text_evidence_available for item in incomplete) or any(
            item.status == CoverageStatus.PARTIAL and item.text_chunk_ids
            for item in incomplete_documents
        ):
            return ReadinessDecision.NEEDS_TEXT_EXTRACTION
        if evidence.unresolved_tables or profiles.failures:
            return ReadinessDecision.NEEDS_CANDIDATE_RESCUE
        return ReadinessDecision.NEEDS_RETRIEVAL_REPAIR
    if not _join_is_supported(
        requirements=requirements,
        evidence=evidence,
        profiles=profiles,
    ):
        return ReadinessDecision.NEEDS_CLARIFICATION
    return ReadinessDecision.READY


def _build_artifact(
    *,
    requirements: AnalysisRequirements,
    evidence: EvidencePackage,
    profiles: DatasetProfiles,
    retrieval: RetrievalResult,
    coverage: tuple[RequirementCoverage, ...],
    ambiguity_model: str,
    deterministic_match_count: int,
    ambiguity_candidate_count: int,
    ambiguity_resolved_count: int,
    ambiguity_llm_used: bool,
) -> EvidenceAssessment:
    document_coverage = _document_coverage(
        requirements=requirements,
        evidence=evidence,
        profiles=profiles,
        retrieval=retrieval,
    )
    decision = _readiness(
        requirements=requirements,
        coverage=coverage,
        document_coverage=document_coverage,
        evidence=evidence,
        profiles=profiles,
    )
    return EvidenceAssessment(
        ambiguity_model=ambiguity_model,
        decision=decision,
        coverage=coverage,
        document_coverage=document_coverage,
        required_count=sum(item.required for item in requirements.requirements),
        supported_count=sum(
            item.status == CoverageStatus.SUPPORTED for item in coverage
        ),
        partial_count=sum(item.status == CoverageStatus.PARTIAL for item in coverage),
        missing_count=sum(item.status == CoverageStatus.MISSING for item in coverage),
        conflicting_count=sum(
            item.status == CoverageStatus.CONFLICTING for item in coverage
        ),
        ambiguous_count=sum(
            item.status == CoverageStatus.AMBIGUOUS for item in coverage
        ),
        diagnostics=AssessmentDiagnostics(
            cache_hit=False,
            deterministic_match_count=deterministic_match_count,
            ambiguity_candidate_count=ambiguity_candidate_count,
            ambiguity_resolved_count=ambiguity_resolved_count,
            ambiguity_llm_used=ambiguity_llm_used,
        ),
    )


class EvidenceAssessmentRunner:
    """Assess readiness with deterministic rules and one selective LLM batch."""

    def __init__(
        self,
        *,
        metadata_repository: AssessmentMetadataRepository,
        cache: AssessmentCache,
        matcher: DeterministicEvidenceMatcher | None = None,
        resolver: AmbiguityResolver | None = None,
    ) -> None:
        self._metadata_repository = metadata_repository
        self._cache = cache
        self._matcher = matcher or DeterministicEvidenceMatcher()
        self._resolver = resolver or AmbiguityResolver()

    async def run(
        self,
        *,
        request: AnalysisRequest,
        requirements: AnalysisRequirements,
        retrieval: RetrievalResult,
        evidence: EvidencePackage,
        profiles: DatasetProfiles,
    ) -> AssessmentRunOutcome:
        warnings: list[AnalysisIssue] = []
        metadata_load_failed = False
        try:
            metadata = await self._metadata_repository.load_table_metadata(
                user_id=request.user_id,
                document_ids=request.document_ids,
                table_ids=tuple(item.table_id for item in evidence.datasets),
            )
        except AssessmentMetadataRepositoryError:
            logger.exception("Assessment table metadata load failed")
            metadata = {}
            metadata_load_failed = True
            warnings.append(
                AnalysisIssue(
                    code=IssueCode.ASSESSMENT_METADATA_LOAD_FAILED,
                    severity=IssueSeverity.WARNING,
                    stage=IssueStage.ASSESSMENT,
                    message=(
                        "Compact table summaries could not be loaded; assessment "
                        "continued using dataset profiles."
                    ),
                    retryable=True,
                )
            )

        cache_key = assessment_cache_key(
            requirements=requirements,
            evidence=evidence,
            profiles=profiles,
            retrieval=retrieval,
            ambiguity_model=self._resolver.model,
            metadata_signatures=_metadata_signatures(metadata),
        )
        try:
            cached = await self._cache.load(
                user_id=request.user_id,
                cache_key=cache_key,
            )
        except AssessmentCacheError:
            logger.exception("Evidence assessment cache read failed")
            cached = None
            warnings.append(
                AnalysisIssue(
                    code=IssueCode.ASSESSMENT_CACHE_READ_FAILED,
                    severity=IssueSeverity.WARNING,
                    stage=IssueStage.ASSESSMENT,
                    message="Cached evidence assessment could not be read.",
                    retryable=True,
                )
            )
        if cached is not None and (
            cached.assessor_version == EVIDENCE_ASSESSOR_VERSION
            and cached.ambiguity_prompt_version == AMBIGUITY_PROMPT_VERSION
            and cached.ambiguity_model == self._resolver.model
        ):
            diagnostics = cached.diagnostics.model_copy(update={"cache_hit": True})
            return AssessmentRunOutcome(
                artifact=cached.model_copy(update={"diagnostics": diagnostics}),
                warnings=tuple(warnings),
            )

        matched = self._matcher.match(
            requirements=requirements,
            evidence=evidence,
            profiles=profiles,
            retrieval=retrieval,
            metadata=metadata,
        )
        resolutions = {}
        resolution_failed = False
        if matched.ambiguities:
            try:
                resolutions = await self._resolver.resolve(matched.ambiguities)
            except Exception:
                logger.exception("Evidence ambiguity resolution failed")
                resolution_failed = True
                warnings.append(
                    AnalysisIssue(
                        code=IssueCode.AMBIGUITY_RESOLUTION_FAILED,
                        severity=IssueSeverity.WARNING,
                        stage=IssueStage.ASSESSMENT,
                        message=(
                            "Ambiguous evidence could not be resolved; the "
                            "assessment remains conservative."
                        ),
                        retryable=True,
                    )
                )
        resolved_coverage, resolved_count = _apply_ambiguity_resolutions(
            requirements=requirements,
            coverage=matched.coverage,
            candidates=matched.ambiguities,
            resolutions=resolutions,
        )
        artifact = _build_artifact(
            requirements=requirements,
            evidence=evidence,
            profiles=profiles,
            retrieval=retrieval,
            coverage=resolved_coverage,
            ambiguity_model=self._resolver.model,
            deterministic_match_count=matched.deterministic_match_count,
            ambiguity_candidate_count=len(matched.ambiguities),
            ambiguity_resolved_count=resolved_count,
            ambiguity_llm_used=bool(matched.ambiguities),
        )
        if not metadata_load_failed and not resolution_failed:
            try:
                await self._cache.save(
                    user_id=request.user_id,
                    cache_key=cache_key,
                    assessment=artifact,
                )
            except AssessmentCacheError:
                logger.exception("Evidence assessment cache write failed")
                warnings.append(
                    AnalysisIssue(
                        code=IssueCode.ASSESSMENT_CACHE_WRITE_FAILED,
                        severity=IssueSeverity.WARNING,
                        stage=IssueStage.ASSESSMENT,
                        message="Evidence assessment could not be cached.",
                        retryable=True,
                    )
                )
        return AssessmentRunOutcome(
            artifact=artifact,
            warnings=tuple(warnings),
        )
