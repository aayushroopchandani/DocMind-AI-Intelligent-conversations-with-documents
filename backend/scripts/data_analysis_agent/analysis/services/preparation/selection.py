from __future__ import annotations

from dataclasses import dataclass

from ...models import (
    AnalysisRequirements,
    AugmentedEvidence,
    CoverageStatus,
    DatasetProfile,
    DatasetProfiles,
    DerivedDatasetReference,
    EvidenceAssessment,
    EvidenceFact,
    EvidenceKind,
    EvidencePackage,
    HydratedDatasetReference,
    RequirementKind,
    RequirementItem,
)


_PREPARABLE_REQUIREMENT_KINDS = frozenset(
    {
        RequirementKind.METRIC,
        RequirementKind.TARGET,
        RequirementKind.PERIOD,
        RequirementKind.DIMENSION,
        RequirementKind.UNIT,
        RequirementKind.FILTER,
        RequirementKind.TOPIC,
    }
)


@dataclass(frozen=True, slots=True)
class SelectedDataset:
    dataset: HydratedDatasetReference
    profile: DatasetProfile | None
    requirement_ids: tuple[str, ...]
    is_base_evidence: bool


@dataclass(frozen=True, slots=True)
class PreparationSelection:
    datasets: tuple[SelectedDataset, ...]
    facts: tuple[EvidenceFact, ...]
    derived_datasets: tuple[DerivedDatasetReference, ...]
    non_tabular_requirement_ids: tuple[str, ...]
    rejected_dataset_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Candidate:
    identity: str
    kind: str
    obligations: frozenset[tuple[str, str | None]]
    quality: float
    confidence: float


def _effective_datasets(
    evidence: EvidencePackage,
    augmented: AugmentedEvidence | None,
) -> tuple[dict[str, HydratedDatasetReference], set[str]]:
    values = {item.dataset_id: item for item in evidence.datasets}
    base_ids = set(values)
    if augmented is not None:
        for addition in augmented.added_datasets:
            values[addition.dataset.dataset_id] = addition.dataset
    return values, base_ids


def _effective_profiles(
    profiles: DatasetProfiles,
    augmented: AugmentedEvidence | None,
) -> dict[str, DatasetProfile]:
    values = {item.dataset_id: item for item in profiles.profiles}
    if augmented is not None and augmented.additional_profiles is not None:
        for item in augmented.additional_profiles.profiles:
            values[item.dataset_id] = item
    return values


def _with_spreadsheet_context(
    selected: tuple[SelectedDataset, ...],
    *,
    datasets: dict[str, HydratedDatasetReference],
    profiles_by_id: dict[str, DatasetProfile],
    base_dataset_ids: set[str],
) -> tuple[SelectedDataset, ...]:
    selected_ids = {item.dataset.dataset_id for item in selected}
    context = tuple(
        SelectedDataset(
            dataset=dataset,
            profile=profiles_by_id.get(dataset.dataset_id),
            requirement_ids=(),
            is_base_evidence=dataset.dataset_id in base_dataset_ids,
        )
        for dataset in datasets.values()
        if dataset.source_type == "spreadsheet_range"
        and dataset.dataset_id in profiles_by_id
        and dataset.dataset_id not in selected_ids
    )
    return (*selected, *context)


def _requirement_obligations(
    *,
    requirements: AnalysisRequirements,
    requirement: RequirementItem,
    evidence_document_ids: tuple[str, ...] = (),
) -> frozenset[tuple[str, str | None]]:
    requirement_id = requirement.requirement_id
    kind = requirement.kind
    if requirement.entity_names and evidence_document_ids:
        return frozenset(
            (requirement_id, document_id)
            for document_id in evidence_document_ids
        )
    if (
        requirements.requires_all_selected_documents
        and kind != RequirementKind.TOPIC
    ):
        return frozenset(
            (requirement_id, document_id)
            for document_id in requirements.selected_document_ids
        )
    return frozenset({(requirement_id, None)})


def select_preparation_evidence(
    *,
    requirements: AnalysisRequirements,
    assessment: EvidenceAssessment,
    evidence: EvidencePackage,
    profiles: DatasetProfiles,
    augmented: AugmentedEvidence | None = None,
) -> PreparationSelection:
    """Choose a deterministic minimum sufficient set of tables and facts."""

    datasets, base_dataset_ids = _effective_datasets(evidence, augmented)
    profiles_by_id = _effective_profiles(profiles, augmented)
    if not requirements.source_evidence_required:
        # Keep bounded immutable source context for workbook guards and schema
        # inspection without pretending generated/output fields already exist.
        selected = tuple(
            SelectedDataset(
                dataset=dataset,
                profile=profiles_by_id.get(dataset.dataset_id),
                requirement_ids=(),
                is_base_evidence=dataset.dataset_id in base_dataset_ids,
            )
            for dataset in datasets.values()
            if dataset.dataset_id in profiles_by_id
        )
        selected_ids = {item.dataset.dataset_id for item in selected}
        return PreparationSelection(
            datasets=selected,
            facts=(),
            derived_datasets=(),
            non_tabular_requirement_ids=(),
            rejected_dataset_ids=tuple(
                sorted(set(datasets).difference(selected_ids))
            ),
        )
    facts = {
        item.fact_id: item
        for item in (augmented.facts if augmented is not None else ())
    }
    requirements_by_id = {
        item.requirement_id: item for item in requirements.requirements
    }
    required_requirement_ids = {
        item.requirement_id
        for item in requirements.requirements
        if item.required
    }
    supported_requirement_ids = {
        item.requirement_id
        for item in assessment.coverage
        if item.status == CoverageStatus.SUPPORTED
    }
    eligible_requirement_ids = (
        required_requirement_ids
        if required_requirement_ids
        else supported_requirement_ids
    )
    if not eligible_requirement_ids:
        required_documents = {
            item.document_id
            for item in assessment.document_coverage
            if item.required and item.status == CoverageStatus.SUPPORTED
        }
        best_by_document: dict[str, HydratedDatasetReference] = {}
        for dataset in datasets.values():
            if dataset.document_id not in required_documents:
                continue
            current = best_by_document.get(dataset.document_id)
            candidate_profile = profiles_by_id.get(dataset.dataset_id)
            current_profile = (
                profiles_by_id.get(current.dataset_id)
                if current is not None
                else None
            )
            candidate_rank = (
                candidate_profile.quality_score if candidate_profile else 0.0,
                dataset.retrieval_score or 0.0,
                dataset.dataset_id,
            )
            current_rank = (
                (
                    current_profile.quality_score
                    if current_profile is not None
                    else 0.0
                ),
                current.retrieval_score or 0.0,
                current.dataset_id,
            ) if current is not None else (-1.0, -1.0, "")
            if candidate_rank > current_rank:
                best_by_document[dataset.document_id] = dataset
        selected_ids = {
            dataset.dataset_id for dataset in best_by_document.values()
        }
        selected = tuple(
            SelectedDataset(
                dataset=dataset,
                profile=profiles_by_id.get(dataset.dataset_id),
                requirement_ids=(),
                is_base_evidence=dataset.dataset_id in base_dataset_ids,
            )
            for _, dataset in sorted(best_by_document.items())
        )
        if requirements.workbook_context_required:
            selected = _with_spreadsheet_context(
                selected,
                datasets=datasets,
                profiles_by_id=profiles_by_id,
                base_dataset_ids=base_dataset_ids,
            )
        selected_ids = {item.dataset.dataset_id for item in selected}
        return PreparationSelection(
            datasets=selected,
            facts=(),
            derived_datasets=(),
            non_tabular_requirement_ids=(),
            rejected_dataset_ids=tuple(sorted(set(datasets) - selected_ids)),
        )

    universe: set[tuple[str, str | None]] = set()
    candidates: dict[str, _Candidate] = {}
    confidence_by_candidate: dict[str, float] = {}

    for coverage in assessment.coverage:
        requirement = requirements_by_id.get(coverage.requirement_id)
        if (
            requirement is None
            or requirement.requirement_id not in eligible_requirement_ids
            or coverage.status != CoverageStatus.SUPPORTED
            or requirement.kind not in _PREPARABLE_REQUIREMENT_KINDS
        ):
            continue
        obligations = _requirement_obligations(
            requirements=requirements,
            requirement=requirement,
            evidence_document_ids=tuple(
                dict.fromkeys(
                    reference.document_id for reference in coverage.evidence
                )
            ),
        )
        universe.update(obligations)
        for reference in coverage.evidence:
            identity: str | None = None
            kind = ""
            if (
                reference.evidence_kind
                in {EvidenceKind.DATASET, EvidenceKind.DATASET_COLUMN}
                and reference.dataset_id in datasets
            ):
                identity = reference.dataset_id
                kind = "dataset"
            elif (
                reference.evidence_kind == EvidenceKind.FACT
                and reference.fact_id in facts
            ):
                identity = reference.fact_id
                kind = "fact"
            if identity is None:
                continue
            covered = frozenset(
                obligation
                for obligation in obligations
                if obligation[1] is None
                or obligation[1] == reference.document_id
            )
            if not covered:
                continue
            previous = candidates.get(identity)
            profile = profiles_by_id.get(identity)
            quality = profile.quality_score if profile is not None else 0.0
            candidates[identity] = _Candidate(
                identity=identity,
                kind=kind,
                obligations=(
                    covered
                    if previous is None
                    else previous.obligations | covered
                ),
                quality=max(quality, previous.quality if previous else 0.0),
                confidence=max(
                    reference.confidence,
                    confidence_by_candidate.get(identity, 0.0),
                ),
            )
            confidence_by_candidate[identity] = candidates[identity].confidence

    derived_datasets = {
        item.derived_dataset_id: item
        for item in (
            augmented.derived_datasets if augmented is not None else ()
        )
    }
    for derived in derived_datasets.values():
        obligations: set[tuple[str, str | None]] = set()
        confidence = 0.0
        for requirement_id in derived.requirement_ids:
            requirement = requirements_by_id.get(requirement_id)
            if (
                requirement is None
                or requirement.requirement_id not in eligible_requirement_ids
                or requirement_id not in supported_requirement_ids
                or requirement.kind not in _PREPARABLE_REQUIREMENT_KINDS
            ):
                continue
            for obligation in _requirement_obligations(
                requirements=requirements,
                requirement=requirement,
                evidence_document_ids=(derived.document_id,),
            ):
                if obligation[1] is None or obligation[1] == derived.document_id:
                    obligations.add(obligation)
            confidence = max(
                confidence,
                max(
                    (
                        fact.confidence
                        for fact in facts.values()
                        if (
                            fact.requirement_id == requirement_id
                            and fact.document_id == derived.document_id
                        )
                    ),
                    default=0.9,
                ),
            )
        if obligations:
            candidates[derived.derived_dataset_id] = _Candidate(
                identity=derived.derived_dataset_id,
                kind="derived",
                obligations=frozenset(obligations),
                quality=1.0,
                confidence=confidence,
            )

    remaining = set(universe)
    selected_ids: list[str] = []
    available = dict(candidates)
    while remaining and available:
        ranked = sorted(
            available.values(),
            key=lambda item: (
                -len(item.obligations & remaining),
                -{"dataset": 2, "derived": 1, "fact": 0}[item.kind],
                -item.quality,
                -item.confidence,
                item.identity,
            ),
        )
        best = ranked[0]
        gain = best.obligations & remaining
        if not gain:
            break
        selected_ids.append(best.identity)
        remaining.difference_update(gain)
        available.pop(best.identity, None)

    selected_dataset_ids = {
        identity for identity in selected_ids if identity in datasets
    }
    selected_fact_ids = {
        identity for identity in selected_ids if identity in facts
    }
    selected_derived_ids = {
        identity for identity in selected_ids if identity in derived_datasets
    }
    requirement_ids_by_dataset: dict[str, list[str]] = {
        dataset_id: [] for dataset_id in selected_dataset_ids
    }
    for coverage in assessment.coverage:
        requirement = requirements_by_id.get(coverage.requirement_id)
        if (
            requirement is None
            or requirement.requirement_id not in eligible_requirement_ids
            or coverage.status != CoverageStatus.SUPPORTED
        ):
            continue
        for reference in coverage.evidence:
            if reference.dataset_id in selected_dataset_ids:
                requirement_ids_by_dataset[reference.dataset_id].append(
                    coverage.requirement_id
                )

    selected_datasets = tuple(
        SelectedDataset(
            dataset=datasets[dataset_id],
            profile=profiles_by_id.get(dataset_id),
            requirement_ids=tuple(
                dict.fromkeys(requirement_ids_by_dataset[dataset_id])
            ),
            is_base_evidence=dataset_id in base_dataset_ids,
        )
        for dataset_id in selected_ids
        if dataset_id in selected_dataset_ids
    )
    if requirements.workbook_context_required:
        selected_datasets = _with_spreadsheet_context(
            selected_datasets,
            datasets=datasets,
            profiles_by_id=profiles_by_id,
            base_dataset_ids=base_dataset_ids,
        )
    final_selected_dataset_ids = {
        item.dataset.dataset_id for item in selected_datasets
    }
    selected_facts = tuple(
        facts[fact_id]
        for fact_id in selected_ids
        if fact_id in selected_fact_ids
    )
    selected_derived = tuple(
        derived_datasets[dataset_id]
        for dataset_id in selected_ids
        if dataset_id in selected_derived_ids
    )
    analytical_requirement_ids = {
        item.requirement_id
        for item in requirements.requirements
        if item.requirement_id in eligible_requirement_ids
        and item.kind in _PREPARABLE_REQUIREMENT_KINDS
    }
    prepared_requirement_ids = {
        requirement_id
        for item in selected_datasets
        for requirement_id in item.requirement_ids
    } | {
        item.requirement_id for item in selected_facts
    } | {
        requirement_id
        for item in selected_derived
        for requirement_id in item.requirement_ids
    }
    non_tabular = tuple(
        sorted(analytical_requirement_ids - prepared_requirement_ids)
    )
    rejected = tuple(
        sorted(set(datasets) - final_selected_dataset_ids)
    )
    return PreparationSelection(
        datasets=selected_datasets,
        facts=selected_facts,
        derived_datasets=selected_derived,
        non_tabular_requirement_ids=non_tabular,
        rejected_dataset_ids=rejected,
    )
