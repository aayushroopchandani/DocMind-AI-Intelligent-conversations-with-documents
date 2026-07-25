from __future__ import annotations

from dataclasses import dataclass

from ...models import (
    AnalysisRequirements,
    CoverageStatus,
    EvidenceAssessment,
    RequirementItem,
    RequirementKind,
    TableCandidateReference,
)
from ..assessment.rules import contains_phrase, lexical_score, normalized_phrase


_SUBSTANTIVE_KINDS = frozenset(
    {
        RequirementKind.METRIC,
        RequirementKind.DIMENSION,
        RequirementKind.FILTER,
        RequirementKind.TOPIC,
    }
)


@dataclass(frozen=True, slots=True)
class RescueSelection:
    candidate: TableCandidateReference
    requirement_ids: tuple[str, ...]
    score: float


def _candidate_fields(candidate: TableCandidateReference) -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            candidate.title,
            candidate.summary,
            *candidate.expected_columns,
            *candidate.expected_metrics,
            *candidate.expected_units,
            *candidate.keywords,
        )
        if value
    )


def _requirement_score(
    requirement: RequirementItem,
    candidate: TableCandidateReference,
) -> float:
    terms = (requirement.name, *requirement.aliases)
    fields = _candidate_fields(candidate)
    best = 0.0
    for term in terms:
        normalized = normalized_phrase(term)
        relaxed = normalized.removeprefix("total ")
        for field in fields:
            if contains_phrase(field, term):
                best = max(best, 1.0)
            else:
                best = max(best, lexical_score(term, field))
            if (
                relaxed != normalized
                and relaxed
                and contains_phrase(field, relaxed)
            ):
                best = max(best, 0.82)
    if requirement.kind == RequirementKind.FILTER and requirement.filter_values:
        combined = " ".join(fields)
        values_supported = all(
            contains_phrase(combined, value)
            or lexical_score(value, combined) >= 0.94
            for value in requirement.filter_values
        )
        if not values_supported:
            return 0.0
    return best


def _period_score(
    requirement: RequirementItem,
    candidate: TableCandidateReference,
) -> float:
    content = " ".join(_candidate_fields(candidate))
    return 1.0 if contains_phrase(content, requirement.name) else 0.0


class CandidateRescueSelector:
    """Select bounded, requirement-specific pre-fusion candidates."""

    def __init__(
        self,
        *,
        max_per_document: int = 2,
        max_total: int = 8,
        minimum_substantive_score: float = 0.50,
    ) -> None:
        self._max_per_document = max(1, max_per_document)
        self._max_total = max(1, max_total)
        self._minimum_substantive_score = minimum_substantive_score

    def select(
        self,
        *,
        requirements: AnalysisRequirements,
        assessment: EvidenceAssessment,
        candidates: tuple[TableCandidateReference, ...],
        used_table_ids: set[str],
    ) -> tuple[RescueSelection, ...]:
        requirements_by_id = {
            item.requirement_id: item for item in requirements.requirements
        }
        incomplete_ids = {
            item.requirement_id
            for item in assessment.coverage
            if item.status != CoverageStatus.SUPPORTED
            and requirements_by_id.get(item.requirement_id) is not None
            and requirements_by_id[item.requirement_id].required
        }
        if not incomplete_ids:
            return ()

        incomplete = tuple(
            item
            for item in requirements.requirements
            if item.requirement_id in incomplete_ids
        )
        substantive = tuple(
            item for item in incomplete if item.kind in _SUBSTANTIVE_KINDS
        )
        if not substantive:
            substantive = tuple(
                item
                for item in requirements.requirements
                if item.required and item.kind in _SUBSTANTIVE_KINDS
            )
        periods = tuple(
            item
            for item in requirements.requirements
            if item.required and item.kind == RequirementKind.PERIOD
        )
        units = tuple(
            item
            for item in requirements.requirements
            if item.required and item.kind == RequirementKind.UNIT
        )
        target_documents = {
            item.document_id
            for item in assessment.document_coverage
            if item.required and item.status != CoverageStatus.SUPPORTED
        }
        if not target_documents:
            target_documents = set(requirements.selected_document_ids)

        ranked: list[RescueSelection] = []
        for candidate in candidates:
            if (
                candidate.table_id in used_table_ids
                or candidate.document_id not in target_documents
            ):
                continue
            matched_ids: list[str] = []
            substantive_score = 0.0
            constraint_scores: list[float] = []
            primary_scores: list[float] = []
            for requirement in substantive:
                score = _requirement_score(requirement, candidate)
                if requirement.kind in {
                    RequirementKind.DIMENSION,
                    RequirementKind.FILTER,
                }:
                    constraint_scores.append(score)
                else:
                    primary_scores.append(score)
                if score >= self._minimum_substantive_score:
                    substantive_score = max(substantive_score, score)
                    if requirement.requirement_id in incomplete_ids:
                        matched_ids.append(requirement.requirement_id)
            if (
                any(
                    score < self._minimum_substantive_score
                    for score in constraint_scores
                )
                or (
                    primary_scores
                    and max(primary_scores) < self._minimum_substantive_score
                )
                or substantive_score < self._minimum_substantive_score
            ):
                continue
            period_score = 0.0
            for requirement in periods:
                score = _period_score(requirement, candidate)
                period_score = max(period_score, score)
                if score and requirement.requirement_id in incomplete_ids:
                    matched_ids.append(requirement.requirement_id)
            unit_score = 0.0
            for requirement in units:
                score = max(
                    (
                        lexical_score(requirement.name, unit)
                        for unit in candidate.expected_units
                    ),
                    default=0.0,
                )
                unit_score = max(unit_score, score)
                if (
                    score >= 0.80
                    and requirement.requirement_id in incomplete_ids
                ):
                    matched_ids.append(requirement.requirement_id)
            if not matched_ids:
                continue
            rrf = min(1.0, float(candidate.rrf_score or 0.0) * 8)
            ranked.append(
                RescueSelection(
                    candidate=candidate,
                    requirement_ids=tuple(dict.fromkeys(matched_ids)),
                    score=round(
                        (0.65 * substantive_score)
                        + (0.15 * period_score)
                        + (0.15 * unit_score)
                        + (0.05 * rrf),
                        6,
                    ),
                )
            )

        ranked.sort(
            key=lambda item: (
                -item.score,
                item.candidate.document_id,
                item.candidate.table_id,
            )
        )
        document_counts: dict[str, int] = {}
        selected: list[RescueSelection] = []
        for item in ranked:
            document_id = item.candidate.document_id
            if document_counts.get(document_id, 0) >= self._max_per_document:
                continue
            selected.append(item)
            document_counts[document_id] = document_counts.get(document_id, 0) + 1
            if len(selected) == self._max_total:
                break
        return tuple(selected)
