from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping

from ...models import (
    ColumnProfile,
    DatasetProfile,
    DatasetProfiles,
    EvidencePackage,
    HydratedDatasetReference,
    ProfiledDataType,
    RetrievalResult,
    SemanticRole,
)
from ...models.assessment import (
    CoverageStatus,
    EvidenceKind,
    EvidenceReference,
    MatchMethod,
    RequirementCoverage,
)
from ...models.requirements import (
    AnalysisRequirements,
    ExpectedDataType,
    RequirementItem,
    RequirementKind,
)
from ...repositories.assessment_metadata import TableAssessmentMetadata
from ..profiling.inference import parse_period
from .rules import (
    contains_phrase,
    document_display_name,
    lexical_score,
    normalized_phrase,
    safe_equivalent,
)


_NUMERIC_TYPES = frozenset(
    {ProfiledDataType.INTEGER, ProfiledDataType.NUMBER}
)
_TEMPORAL_TYPES = frozenset(
    {
        ProfiledDataType.CALENDAR_YEAR,
        ProfiledDataType.FISCAL_PERIOD,
        ProfiledDataType.QUARTER,
        ProfiledDataType.MONTH,
        ProfiledDataType.DATE,
    }
)
_MAX_EVIDENCE_PER_REQUIREMENT = 12
_MAX_AMBIGUITIES = 20


@dataclass(frozen=True, slots=True)
class DatasetContext:
    dataset: HydratedDatasetReference
    profile: DatasetProfile
    metadata: TableAssessmentMetadata | None


@dataclass(frozen=True, slots=True)
class AmbiguityCandidate:
    pair_id: str
    requirement: RequirementItem
    evidence: EvidenceReference
    lexical_confidence: float
    table_title: str
    table_summary: str
    columns: tuple[ColumnProfile, ...]


@dataclass(frozen=True, slots=True)
class MatchingResult:
    coverage: tuple[RequirementCoverage, ...]
    ambiguities: tuple[AmbiguityCandidate, ...]
    deterministic_match_count: int


def _profile_contexts(
    *,
    evidence: EvidencePackage,
    profiles: DatasetProfiles,
    metadata: Mapping[str, TableAssessmentMetadata],
) -> tuple[DatasetContext, ...]:
    datasets = {item.dataset_id: item for item in evidence.datasets}
    contexts: list[DatasetContext] = []
    for profile in profiles.profiles:
        dataset = datasets.get(profile.dataset_id)
        if dataset is None or dataset.source_version != profile.source_version:
            continue
        contexts.append(
            DatasetContext(
                dataset=dataset,
                profile=profile,
                metadata=metadata.get(profile.table_id),
            )
        )
    return tuple(contexts)


def _dataset_reference(
    context: DatasetContext,
    *,
    confidence: float,
    method: MatchMethod,
    label: str,
    column: ColumnProfile | None = None,
) -> EvidenceReference:
    return EvidenceReference(
        evidence_kind=(
            EvidenceKind.DATASET_COLUMN if column else EvidenceKind.DATASET
        ),
        dataset_id=context.dataset.dataset_id,
        source_version=context.dataset.source_version,
        table_id=context.dataset.table_id,
        document_id=context.dataset.document_id,
        column_key=column.key if column else None,
        label=label[:240],
        confidence=round(min(1.0, max(0.0, confidence)), 4),
        match_method=method,
    )


def _text_reference(
    *,
    chunk_id: str,
    document_id: str,
    label: str,
    confidence: float,
) -> EvidenceReference:
    return EvidenceReference(
        evidence_kind=EvidenceKind.TEXT_CHUNK,
        chunk_id=chunk_id,
        document_id=document_id,
        label=label[:240],
        confidence=confidence,
        match_method=MatchMethod.EXACT,
    )


def _requirement_terms(item: RequirementItem) -> tuple[str, ...]:
    return tuple(dict.fromkeys((item.name, *item.aliases)))


def _entity_scope(
    entity_names: Iterable[str],
    contexts: tuple[DatasetContext, ...],
    retrieval: RetrievalResult,
) -> tuple[set[str], tuple[str, ...]]:
    names_by_document: dict[str, set[str]] = defaultdict(set)
    for context in contexts:
        names_by_document[context.dataset.document_id].update(
            {
                context.dataset.document_name,
                document_display_name(context.dataset.document_name),
            }
        )
    for chunk in retrieval.text_evidence:
        names_by_document[chunk.document_id].update(
            {
                chunk.document_name,
                document_display_name(chunk.document_name),
                chunk.text,
            }
        )
    output: set[str] = set()
    unresolved: list[str] = []
    for entity in entity_names:
        matched_entity = False
        for document_id, names in names_by_document.items():
            if any(
                contains_phrase(name, entity)
                or lexical_score(entity, name) >= 0.76
                for name in names
                if name
            ):
                output.add(document_id)
                matched_entity = True
        if not matched_entity:
            unresolved.append(entity)
    return output, tuple(unresolved)


def _required_document_ids(
    item: RequirementItem,
    requirements: AnalysisRequirements,
    contexts: tuple[DatasetContext, ...],
    retrieval: RetrievalResult,
) -> tuple[set[str], tuple[str, ...]]:
    if item.entity_names:
        return _entity_scope(item.entity_names, contexts, retrieval)
    if requirements.requires_all_selected_documents and item.kind in {
        RequirementKind.METRIC,
        RequirementKind.PERIOD,
        RequirementKind.DIMENSION,
        RequirementKind.UNIT,
    }:
        return set(requirements.selected_document_ids), ()
    return set(), ()


def _compatible_column(
    requirement: RequirementItem,
    column: ColumnProfile,
) -> bool:
    if requirement.expected_data_type == ExpectedDataType.NUMBER:
        return column.inferred_type in _NUMERIC_TYPES
    if requirement.expected_data_type == ExpectedDataType.DATE:
        return (
            column.inferred_type in _TEMPORAL_TYPES
            or column.semantic_role == SemanticRole.TIME_PERIOD
        )
    if requirement.expected_data_type == ExpectedDataType.BOOLEAN:
        return (
            column.inferred_type == ProfiledDataType.BOOLEAN
            or column.semantic_role == SemanticRole.BOOLEAN_FLAG
        )
    return True


def _column_role_compatible(
    requirement: RequirementItem,
    column: ColumnProfile,
) -> bool:
    if requirement.kind == RequirementKind.METRIC:
        return (
            column.semantic_role in {SemanticRole.METRIC, SemanticRole.UNKNOWN}
            or column.inferred_type in _NUMERIC_TYPES
        )
    if requirement.kind in {RequirementKind.DIMENSION, RequirementKind.FILTER}:
        return column.semantic_role in {
            SemanticRole.DIMENSION,
            SemanticRole.CATEGORY,
            SemanticRole.IDENTIFIER,
            SemanticRole.TIME_PERIOD,
            SemanticRole.UNKNOWN,
        }
    return True


def _best_label_score(
    requirement: RequirementItem,
    candidate: str,
) -> tuple[float, MatchMethod]:
    direct = lexical_score(requirement.name, candidate)
    best = direct
    method = MatchMethod.EXACT if direct == 1.0 else MatchMethod.LEXICAL
    for alias in requirement.aliases:
        alias_score = lexical_score(alias, candidate)
        if alias_score > best:
            best = alias_score
            method = (
                MatchMethod.VERIFIED_ALIAS
                if alias_score >= 0.94
                else MatchMethod.LEXICAL
            )
    return best, method


def _column_matches(
    requirement: RequirementItem,
    context: DatasetContext,
) -> tuple[
    list[EvidenceReference],
    list[AmbiguityCandidate],
    bool,
    bool,
]:
    matches: list[EvidenceReference] = []
    ambiguities: list[AmbiguityCandidate] = []
    unit_conflict = False
    verified_unit = False
    for column in context.profile.columns:
        if not _compatible_column(requirement, column):
            continue
        if not _column_role_compatible(requirement, column):
            continue
        if requirement.unit:
            if column.detected_unit:
                unit_score = lexical_score(
                    requirement.unit,
                    column.detected_unit,
                )
                if unit_score < 0.80:
                    unit_conflict = True
                    continue
                verified_unit = True
        score, method = _best_label_score(
            requirement,
            f"{column.label} {column.key}",
        )
        if score >= 0.82:
            matches.append(
                _dataset_reference(
                    context,
                    confidence=score,
                    method=method,
                    label=f"{context.profile.title}: {column.label}",
                    column=column,
                )
            )
        elif score >= 0.43:
            evidence = _dataset_reference(
                context,
                confidence=score,
                method=MatchMethod.LEXICAL,
                label=f"{context.profile.title}: {column.label}",
                column=column,
            )
            ambiguities.append(
                AmbiguityCandidate(
                    pair_id=(
                        f"{requirement.requirement_id}:"
                        f"{context.dataset.dataset_id}:{column.key}"
                    ),
                    requirement=requirement,
                    evidence=evidence,
                    lexical_confidence=score,
                    table_title=context.profile.title,
                    table_summary=(
                        context.metadata.summary if context.metadata else ""
                    ),
                    columns=context.profile.columns,
                )
            )
    return matches, ambiguities, unit_conflict, verified_unit


def _summary_matches(
    requirement: RequirementItem,
    context: DatasetContext,
) -> list[EvidenceReference]:
    metadata = context.metadata
    if metadata is None:
        return []
    if (
        requirement.expected_data_type == ExpectedDataType.NUMBER
        and not any(
            column.inferred_type in _NUMERIC_TYPES
            for column in context.profile.columns
        )
    ):
        return []
    terms = _requirement_terms(requirement)
    for keyword in metadata.keywords:
        if any(safe_equivalent(term, keyword) for term in terms):
            return [
                _dataset_reference(
                    context,
                    confidence=0.90,
                    method=MatchMethod.VERIFIED_ALIAS,
                    label=f"{context.profile.title}: {keyword}",
                )
            ]
    combined = f"{metadata.title}\n{metadata.summary}"
    if any(contains_phrase(combined, term) for term in terms):
        return [
            _dataset_reference(
                context,
                confidence=0.84,
                method=MatchMethod.TABLE_SUMMARY,
                label=context.profile.title,
            )
        ]
    return []


def _period_matches(
    requirement: RequirementItem,
    context: DatasetContext,
) -> list[EvidenceReference]:
    requested = parse_period(requirement.name)
    if requested is None:
        return []
    matches: list[EvidenceReference] = []
    for column in context.profile.columns:
        header_period = parse_period(column.label, label=column.label)
        if header_period and header_period.label == requested.label:
            matches.append(
                _dataset_reference(
                    context,
                    confidence=1.0,
                    method=MatchMethod.PROFILE_PERIOD,
                    label=f"{context.profile.title}: {column.label}",
                    column=column,
                )
            )
            continue
        statistics = column.time_statistics
        if statistics is None:
            continue
        if requested.label in {
            statistics.minimum_period,
            statistics.maximum_period,
        } or any(
            parse_period(value) is not None
            and parse_period(value).label == requested.label
            for value in column.example_values
        ):
            if requested.label not in statistics.missing_intervals:
                matches.append(
                    _dataset_reference(
                        context,
                        confidence=0.96,
                        method=MatchMethod.PROFILE_PERIOD,
                        label=f"{context.profile.title}: {column.label}",
                        column=column,
                    )
                )
                continue
        minimum = (
            parse_period(statistics.minimum_period)
            if statistics.minimum_period
            else None
        )
        maximum = (
            parse_period(statistics.maximum_period)
            if statistics.maximum_period
            else None
        )
        if (
            minimum
            and maximum
            and minimum.sort_key <= requested.sort_key <= maximum.sort_key
            and requested.label not in statistics.missing_intervals
        ):
            matches.append(
                _dataset_reference(
                    context,
                    confidence=0.90,
                    method=MatchMethod.PROFILE_PERIOD,
                    label=f"{context.profile.title}: {column.label}",
                    column=column,
                )
            )
    return matches


def _unit_matches(
    requirement: RequirementItem,
    context: DatasetContext,
) -> tuple[list[EvidenceReference], bool]:
    matches: list[EvidenceReference] = []
    conflicting = False
    for column in context.profile.columns:
        unit = column.detected_unit
        if not unit:
            continue
        if (
            lexical_score(requirement.name, unit) >= 0.80
            or contains_phrase(unit, requirement.name)
            or contains_phrase(requirement.name, unit)
        ):
            matches.append(
                _dataset_reference(
                    context,
                    confidence=0.96,
                    method=MatchMethod.UNIT,
                    label=f"{context.profile.title}: {column.label} ({unit})",
                    column=column,
                )
            )
        elif column.semantic_role == SemanticRole.METRIC:
            conflicting = True
    return matches, conflicting


def _entity_matches(
    requirement: RequirementItem,
    context: DatasetContext,
) -> list[EvidenceReference]:
    candidates = (
        context.dataset.document_name,
        document_display_name(context.dataset.document_name),
        context.profile.title,
    )
    best = max(
        (
            lexical_score(term, candidate)
            for term in _requirement_terms(requirement)
            for candidate in candidates
            if candidate
        ),
        default=0.0,
    )
    if best < 0.76:
        return []
    return [
        _dataset_reference(
            context,
            confidence=max(0.88, best),
            method=MatchMethod.DOCUMENT_SCOPE,
            label=context.dataset.document_name or context.profile.title,
        )
    ]


def _text_matches(
    requirement: RequirementItem,
    retrieval: RetrievalResult,
    *,
    allowed_document_ids: set[str],
) -> list[EvidenceReference]:
    terms = _requirement_terms(requirement)
    output: list[EvidenceReference] = []
    for chunk in retrieval.text_evidence:
        if allowed_document_ids and chunk.document_id not in allowed_document_ids:
            continue
        term_haystack = (
            f"{chunk.document_name}\n{chunk.text}"
            if requirement.kind == RequirementKind.ENTITY
            else chunk.text
        )
        term_match = any(contains_phrase(term_haystack, term) for term in terms)
        entity_match = not requirement.entity_names or any(
            contains_phrase(
                f"{chunk.document_name}\n{chunk.text}",
                entity,
            )
            for entity in requirement.entity_names
        )
        if term_match and entity_match:
            output.append(
                _text_reference(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    label=(
                        f"{chunk.document_name or chunk.document_id}"
                        f"{f' page {chunk.page_number}' if chunk.page_number else ''}"
                    ),
                    confidence=0.72,
                )
            )
    return output[:_MAX_EVIDENCE_PER_REQUIREMENT]


def _deduplicate_evidence(
    values: Iterable[EvidenceReference],
) -> tuple[EvidenceReference, ...]:
    best: dict[tuple[object, ...], EvidenceReference] = {}
    for value in values:
        key = (
            value.evidence_kind,
            value.dataset_id,
            value.column_key,
            value.chunk_id,
        )
        current = best.get(key)
        if current is None or value.confidence > current.confidence:
            best[key] = value
    return tuple(
        sorted(
            best.values(),
            key=lambda item: (
                -item.confidence,
                item.document_id,
                item.dataset_id or "",
                item.column_key or "",
                item.chunk_id or "",
            ),
        )[:_MAX_EVIDENCE_PER_REQUIREMENT]
    )


def _coverage_from_matches(
    *,
    requirement: RequirementItem,
    dataset_matches: list[EvidenceReference],
    text_matches: list[EvidenceReference],
    ambiguities: list[AmbiguityCandidate],
    required_document_ids: set[str],
    unit_conflict: bool,
    unit_unverified: bool,
    unresolved_entity_names: tuple[str, ...],
    requirements: AnalysisRequirements,
) -> RequirementCoverage:
    evidence = _deduplicate_evidence((*dataset_matches, *text_matches))
    matched_documents = {
        item.document_id
        for item in dataset_matches
        if item.evidence_kind != EvidenceKind.TEXT_CHUNK
    }
    missing_documents = required_document_ids - matched_documents
    if unit_conflict and (not dataset_matches or unit_unverified):
        return RequirementCoverage(
            requirement_id=requirement.requirement_id,
            status=CoverageStatus.CONFLICTING,
            confidence=0.70,
            reason="Available metric columns use a different detected unit.",
            evidence=evidence,
            text_evidence_available=bool(text_matches),
        )
    if (
        dataset_matches
        and not missing_documents
        and not unresolved_entity_names
        and not unit_unverified
    ):
        return RequirementCoverage(
            requirement_id=requirement.requirement_id,
            status=CoverageStatus.SUPPORTED,
            confidence=max(item.confidence for item in dataset_matches),
            reason="Deterministic profile matching found suitable structured evidence.",
            evidence=evidence,
            text_evidence_available=bool(text_matches),
        )
    if dataset_matches and (
        missing_documents or unresolved_entity_names or unit_unverified
    ):
        missing_scope = [
            *sorted(missing_documents),
            *(
                f"entity:{entity}"
                for entity in sorted(unresolved_entity_names)
            ),
        ]
        if unit_unverified:
            missing_scope.append("unit verification")
        return RequirementCoverage(
            requirement_id=requirement.requirement_id,
            status=CoverageStatus.PARTIAL,
            confidence=max(item.confidence for item in dataset_matches),
            reason=(
                "Structured evidence is missing for selected document(s): "
                + ", ".join(missing_scope)
            ),
            evidence=evidence,
            text_evidence_available=bool(text_matches),
        )
    text_can_support = (
        not requirements.table_evidence_required
        and requirements.text_evidence_acceptable
        and requirement.kind
        in {RequirementKind.ENTITY, RequirementKind.TOPIC, RequirementKind.FILTER}
    )
    if text_matches:
        return RequirementCoverage(
            requirement_id=requirement.requirement_id,
            status=(
                CoverageStatus.SUPPORTED
                if text_can_support and not missing_documents
                else CoverageStatus.PARTIAL
            ),
            confidence=max(item.confidence for item in text_matches),
            reason=(
                "Relevant text evidence exists."
                if text_can_support
                else "Relevant text exists but structured values still require extraction."
            ),
            evidence=evidence,
            text_evidence_available=True,
        )
    if ambiguities:
        return RequirementCoverage(
            requirement_id=requirement.requirement_id,
            status=CoverageStatus.AMBIGUOUS,
            confidence=max(item.lexical_confidence for item in ambiguities),
            reason="Only semantically ambiguous structured candidates were found.",
            evidence=(),
        )
    return RequirementCoverage(
        requirement_id=requirement.requirement_id,
        status=CoverageStatus.MISSING,
        confidence=0.0,
        reason="No matching evidence was found in the current retrieval result.",
    )


class DeterministicEvidenceMatcher:
    """Match requirements against compact profiles and references, never rows."""

    def match(
        self,
        *,
        requirements: AnalysisRequirements,
        evidence: EvidencePackage,
        profiles: DatasetProfiles,
        retrieval: RetrievalResult,
        metadata: Mapping[str, TableAssessmentMetadata],
    ) -> MatchingResult:
        contexts = _profile_contexts(
            evidence=evidence,
            profiles=profiles,
            metadata=metadata,
        )
        coverage: list[RequirementCoverage] = []
        all_ambiguities: list[AmbiguityCandidate] = []
        deterministic_count = 0

        for requirement in requirements.requirements:
            required_documents, unresolved_entities = _required_document_ids(
                requirement,
                requirements,
                contexts,
                retrieval,
            )
            scoped_contexts = (
                ()
                if requirement.entity_names and not required_documents
                else tuple(
                    context
                    for context in contexts
                    if not required_documents
                    or context.dataset.document_id in required_documents
                )
            )
            dataset_matches: list[EvidenceReference] = []
            ambiguities: list[AmbiguityCandidate] = []
            unit_conflict = False
            verified_unit_match = False

            for context in scoped_contexts:
                if not context.profile.suitable_for_analysis and requirement.kind not in {
                    RequirementKind.ENTITY,
                    RequirementKind.TOPIC,
                }:
                    continue
                if requirement.kind == RequirementKind.PERIOD:
                    dataset_matches.extend(_period_matches(requirement, context))
                elif requirement.kind == RequirementKind.UNIT:
                    matches, conflict = _unit_matches(requirement, context)
                    dataset_matches.extend(matches)
                    unit_conflict = unit_conflict or conflict
                elif requirement.kind == RequirementKind.ENTITY:
                    dataset_matches.extend(_entity_matches(requirement, context))
                elif requirement.kind in {
                    RequirementKind.METRIC,
                    RequirementKind.DIMENSION,
                    RequirementKind.FILTER,
                    RequirementKind.TOPIC,
                }:
                    (
                        matches,
                        candidates,
                        column_unit_conflict,
                        column_unit_verified,
                    ) = _column_matches(requirement, context)
                    dataset_matches.extend(matches)
                    ambiguities.extend(candidates)
                    unit_conflict = unit_conflict or column_unit_conflict
                    verified_unit_match = (
                        verified_unit_match or column_unit_verified
                    )
                    dataset_matches.extend(_summary_matches(requirement, context))

            text_matches = _text_matches(
                requirement,
                retrieval,
                allowed_document_ids=required_documents,
            )
            item_coverage = _coverage_from_matches(
                requirement=requirement,
                dataset_matches=dataset_matches,
                text_matches=text_matches,
                ambiguities=ambiguities,
                required_document_ids=required_documents,
                unit_conflict=unit_conflict,
                unit_unverified=bool(
                    requirement.unit
                    and dataset_matches
                    and not verified_unit_match
                ),
                unresolved_entity_names=unresolved_entities,
                requirements=requirements,
            )
            coverage.append(item_coverage)
            deterministic_count += sum(
                item.match_method != MatchMethod.LLM
                for item in item_coverage.evidence
            )
            has_structured_evidence = any(
                item.evidence_kind != EvidenceKind.TEXT_CHUNK
                for item in item_coverage.evidence
            )
            if (
                item_coverage.status
                in {CoverageStatus.AMBIGUOUS, CoverageStatus.PARTIAL}
                and not has_structured_evidence
            ):
                remaining = _MAX_AMBIGUITIES - len(all_ambiguities)
                if remaining > 0:
                    all_ambiguities.extend(
                        sorted(
                            ambiguities,
                            key=lambda value: -value.lexical_confidence,
                        )[:remaining]
                    )

        return MatchingResult(
            coverage=tuple(coverage),
            ambiguities=tuple(all_ambiguities),
            deterministic_match_count=deterministic_count,
        )
