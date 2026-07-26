from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, replace
from time import perf_counter
from typing import Any, Sequence

from pydantic import ValidationError

from db.models.structured_table import StructuredTable

from ...models import (
    DATASET_NORMALIZER_VERSION,
    AnalysisIssue,
    AnalysisRequirements,
    AugmentedEvidence,
    DatasetPreparationFailure,
    DatasetProfiles,
    EvidenceAssessment,
    EvidencePackage,
    IssueCode,
    IssueSeverity,
    IssueStage,
    MaterializationType,
    NormalizationResult,
    NormalizationStatus,
    NormalizedDatasetReference,
    PreparationFailureReason,
    PreparedDatasetAccessReference,
    ReadinessDecision,
)
from ...repositories import (
    DatasetRepository,
    DatasetRepositoryError,
    NormalizedDatasetRepository,
    NormalizedDatasetRepositoryError,
    NormalizedDatasetWrite,
)
from ..versioning import source_version
from .recipe import (
    CleaningRecipe,
    build_cleaning_recipe,
    normalized_dataset_id,
    preparation_cache_key,
)
from .selection import SelectedDataset, select_preparation_evidence
from .transform import DeterministicDatasetTransformer, TransformOutput


logger = logging.getLogger(__name__)
DEFAULT_PREPARATION_CONCURRENCY = 4
MAX_PREPARATION_CONCURRENCY = 8


@dataclass(frozen=True, slots=True)
class PreparationRunOutcome:
    artifact: NormalizationResult
    warnings: tuple[AnalysisIssue, ...] = ()
    errors: tuple[AnalysisIssue, ...] = ()
    total_latency_ms: float = 0.0
    dataset_latencies_ms: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class _PreparedWork:
    dataset_id: str
    reference: NormalizedDatasetReference | None = None
    failure: DatasetPreparationFailure | None = None
    issue: AnalysisIssue | None = None
    warnings: tuple[AnalysisIssue, ...] = ()
    duration_ms: float = 0.0


def _input_signature(
    *,
    selected: tuple[SelectedDataset, ...],
    recipes: dict[str, CleaningRecipe],
    fact_ids: tuple[str, ...],
    derived_dataset_ids: tuple[str, ...],
) -> str:
    payload = {
        "normalizer_version": DATASET_NORMALIZER_VERSION,
        "datasets": sorted(
            (
                item.dataset.dataset_id,
                item.dataset.source_version,
                recipes[item.dataset.dataset_id].recipe_hash,
            )
            for item in selected
            if item.dataset.dataset_id in recipes
        ),
        "facts": sorted(fact_ids),
        "derived_datasets": sorted(derived_dataset_ids),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _failure(
    selected: SelectedDataset,
    *,
    reason: PreparationFailureReason,
    message: str,
    code: IssueCode,
    retryable: bool = False,
) -> _PreparedWork:
    dataset = selected.dataset
    return _PreparedWork(
        dataset_id=dataset.dataset_id,
        failure=DatasetPreparationFailure(
            dataset_id=dataset.dataset_id,
            source_version=dataset.source_version,
            table_id=dataset.table_id,
            document_id=dataset.document_id,
            requirement_ids=selected.requirement_ids,
            reason=reason,
            message=message,
            retryable=retryable,
        ),
        issue=AnalysisIssue(
            code=code,
            severity=IssueSeverity.ERROR,
            stage=IssueStage.PREPARATION,
            message=message,
            retryable=retryable,
            dataset_id=dataset.dataset_id,
            table_id=dataset.table_id,
            document_id=dataset.document_id,
        ),
    )


def _cached_reference(
    *,
    cached: NormalizedDatasetReference | None,
    selected: SelectedDataset,
    recipe: CleaningRecipe,
    cache_key: str,
) -> NormalizedDatasetReference | None:
    if cached is None:
        return None
    dataset = selected.dataset
    if (
        cached.cache_key != cache_key
        or cached.normalizer_version != DATASET_NORMALIZER_VERSION
        or cached.recipe_hash != recipe.recipe_hash
        or cached.source_dataset_ids != (dataset.dataset_id,)
        or cached.source_versions != (dataset.source_version,)
        or cached.source_table_ids != (dataset.table_id,)
        or cached.document_id != dataset.document_id
        or cached.materialization != recipe.materialization
    ):
        return None
    return cached.model_copy(
        update={
            "requirement_ids": selected.requirement_ids,
            "cache_hit": True,
        }
    )


def _reference(
    *,
    selected: SelectedDataset,
    recipe: CleaningRecipe,
    cache_key: str,
    transformed: TransformOutput,
    input_row_count: int,
) -> NormalizedDatasetReference:
    dataset = selected.dataset
    identifier = normalized_dataset_id(cache_key)
    passthrough = (
        recipe.materialization == MaterializationType.SOURCE_PASSTHROUGH
    )
    return NormalizedDatasetReference(
        normalized_dataset_id=identifier,
        cache_key=cache_key,
        recipe_hash=recipe.recipe_hash,
        materialization=recipe.materialization,
        source_dataset_ids=(dataset.dataset_id,),
        source_versions=(dataset.source_version,),
        source_table_ids=(dataset.table_id,),
        document_id=dataset.document_id,
        source_page_start=dataset.page_start,
        source_page_end=dataset.page_end,
        title=dataset.title,
        requirement_ids=selected.requirement_ids,
        columns=recipe.output_columns,
        input_column_count=len(dataset.columns),
        output_column_count=len(recipe.output_columns),
        input_row_count=input_row_count,
        retained_source_row_count=transformed.retained_source_row_count,
        output_row_count=(
            input_row_count if passthrough else len(transformed.rows)
        ),
        duplicate_row_count=transformed.duplicate_row_count,
        repeated_header_row_count=transformed.repeated_header_row_count,
        footnote_row_count=len(transformed.footnotes),
        total_or_subtotal_row_count=(
            transformed.total_or_subtotal_row_count
        ),
        numeric_parse_failure_count=(
            transformed.numeric_parse_failure_count
        ),
        period_parse_failure_count=transformed.period_parse_failure_count,
        quality_score_before=selected.profile.quality_score,
        quality_score_after=transformed.quality_score_after,
        transformations=transformed.transformations,
        validation_checks=transformed.validation_checks,
        access=PreparedDatasetAccessReference(
            collection=(
                "structured_tables"
                if passthrough
                else "normalized_datasets"
            ),
            record_id=dataset.table_id if passthrough else identifier,
        ),
    )


class DatasetPreparationRunner:
    """Select, normalize, validate, persist and cache analysis-ready datasets."""

    def __init__(
        self,
        *,
        dataset_repository: DatasetRepository,
        normalized_repository: NormalizedDatasetRepository,
        transformer: DeterministicDatasetTransformer | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self._dataset_repository = dataset_repository
        self._normalized_repository = normalized_repository
        self._transformer = transformer or DeterministicDatasetTransformer()
        configured = max_concurrency
        if configured is None:
            try:
                configured = int(
                    os.getenv(
                        "DATA_ANALYSIS_PREPARATION_CONCURRENCY",
                        str(DEFAULT_PREPARATION_CONCURRENCY),
                    )
                )
            except ValueError:
                configured = DEFAULT_PREPARATION_CONCURRENCY
        self._max_concurrency = min(
            MAX_PREPARATION_CONCURRENCY,
            max(1, configured),
        )

    async def run(
        self,
        *,
        run_id: str,
        user_id: str,
        document_ids: Sequence[str],
        requirements: AnalysisRequirements,
        assessment: EvidenceAssessment,
        evidence: EvidencePackage,
        profiles: DatasetProfiles,
        augmented: AugmentedEvidence | None = None,
    ) -> PreparationRunOutcome:
        run_started = perf_counter()
        if assessment.decision != ReadinessDecision.READY:
            raise ValueError(
                "dataset preparation requires a final ready evidence assessment"
            )
        selection = select_preparation_evidence(
            requirements=requirements,
            assessment=assessment,
            evidence=evidence,
            profiles=profiles,
            augmented=augmented,
        )
        recipes: dict[str, CleaningRecipe] = {}
        preflight: dict[str, _PreparedWork] = {}
        for selected in selection.datasets:
            if selected.profile is None:
                preflight[selected.dataset.dataset_id] = _failure(
                    selected,
                    reason=PreparationFailureReason.INVALID_SOURCE,
                    message="The selected dataset has no compatible profile.",
                    code=IssueCode.DATASET_PREPARATION_FAILED,
                )
                continue
            recipes[selected.dataset.dataset_id] = build_cleaning_recipe(
                dataset=selected.dataset,
                profile=selected.profile,
            )

        fact_ids = tuple(item.fact_id for item in selection.facts)
        derived_dataset_ids = tuple(
            item.derived_dataset_id for item in selection.derived_datasets
        )
        signature = _input_signature(
            selected=selection.datasets,
            recipes=recipes,
            fact_ids=fact_ids,
            derived_dataset_ids=derived_dataset_ids,
        )
        if not selection.datasets:
            return PreparationRunOutcome(
                artifact=NormalizationResult(
                    run_id=run_id,
                    input_evidence_signature=signature,
                    status=NormalizationStatus.READY,
                    selected_fact_ids=fact_ids,
                    selected_derived_dataset_ids=derived_dataset_ids,
                    non_tabular_requirement_ids=(
                        selection.non_tabular_requirement_ids
                    ),
                    rejected_dataset_ids=selection.rejected_dataset_ids,
                    selected_dataset_count=0,
                    prepared_dataset_count=0,
                    cache_hit_count=0,
                    passthrough_count=0,
                    materialized_count=0,
                    total_input_rows=0,
                    total_output_rows=0,
                    can_analyze=True,
                ),
                total_latency_ms=round(
                    (perf_counter() - run_started) * 1000,
                    3,
                ),
            )

        cache_keys = {
            selected.dataset.dataset_id: preparation_cache_key(
                dataset_id=selected.dataset.dataset_id,
                source_version=selected.dataset.source_version,
                recipe_hash=recipes[selected.dataset.dataset_id].recipe_hash,
            )
            for selected in selection.datasets
            if selected.dataset.dataset_id in recipes
        }
        cache_task = asyncio.create_task(
            self._normalized_repository.load_many(
                user_id=user_id,
                cache_keys=tuple(cache_keys.values()),
            )
        )
        source_task = asyncio.create_task(
            self._dataset_repository.load_tables(
                user_id=user_id,
                document_ids=document_ids,
                table_ids=tuple(
                    item.dataset.table_id for item in selection.datasets
                ),
            )
        )
        warnings: list[AnalysisIssue] = []
        errors: list[AnalysisIssue] = []
        try:
            cached = await cache_task
        except NormalizedDatasetRepositoryError:
            logger.exception("Prepared dataset cache read failed")
            cached = {}
            warnings.append(
                AnalysisIssue(
                    code=IssueCode.PREPARATION_CACHE_READ_FAILED,
                    severity=IssueSeverity.WARNING,
                    stage=IssueStage.PREPARATION,
                    message="Cached normalized datasets could not be read.",
                    retryable=True,
                )
            )
        try:
            raw_tables = await source_task
        except DatasetRepositoryError:
            logger.exception("Preparation source rows could not be loaded")
            raw_tables = ()
            errors.append(
                AnalysisIssue(
                    code=IssueCode.PREPARATION_DATA_LOAD_FAILED,
                    severity=IssueSeverity.ERROR,
                    stage=IssueStage.PREPARATION,
                    message="Selected source rows could not be loaded.",
                    retryable=True,
                )
            )
        tables_by_id = {
            str(item.get("table_id") or ""): item
            for item in raw_tables
            if str(item.get("table_id") or "")
        }
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def prepare_one_unmeasured(
            selected: SelectedDataset,
        ) -> _PreparedWork:
            dataset = selected.dataset
            existing = preflight.get(dataset.dataset_id)
            if existing is not None:
                return existing
            recipe = recipes[dataset.dataset_id]
            raw = tables_by_id.get(dataset.table_id)
            if raw is None:
                return _failure(
                    selected,
                    reason=PreparationFailureReason.NOT_AVAILABLE,
                    message="Selected source rows are not available.",
                    code=IssueCode.DATASET_PREPARATION_FAILED,
                    retryable=True,
                )
            try:
                table = StructuredTable.model_validate(raw)
            except ValidationError:
                return _failure(
                    selected,
                    reason=PreparationFailureReason.INVALID_SOURCE,
                    message="Selected source rows failed structural validation.",
                    code=IssueCode.DATASET_PREPARATION_FAILED,
                )
            if (
                table.user_id != user_id
                or table.document_id != dataset.document_id
                or table.table_id != dataset.table_id
            ):
                return _failure(
                    selected,
                    reason=PreparationFailureReason.INVALID_SOURCE,
                    message="Selected source identity does not match the request.",
                    code=IssueCode.DATASET_PREPARATION_FAILED,
                )
            if source_version(table) != dataset.source_version:
                return _failure(
                    selected,
                    reason=PreparationFailureReason.SOURCE_VERSION_MISMATCH,
                    message="The source table changed after evidence assessment.",
                    code=IssueCode.PREPARATION_VERSION_MISMATCH,
                    retryable=True,
                )
            cache_key = cache_keys[dataset.dataset_id]
            cache_reference = _cached_reference(
                cached=cached.get(cache_key),
                selected=selected,
                recipe=recipe,
                cache_key=cache_key,
            )
            if cache_reference is not None:
                return _PreparedWork(
                    dataset_id=dataset.dataset_id,
                    reference=cache_reference,
                )
            try:
                async with semaphore:
                    transformed = await asyncio.to_thread(
                        self._transformer.transform,
                        dataset=dataset,
                        profile=selected.profile,
                        table=table,
                        recipe=recipe,
                    )
                reference = _reference(
                    selected=selected,
                    recipe=recipe,
                    cache_key=cache_key,
                    transformed=transformed,
                    input_row_count=len(table.rows),
                )
            except (ValueError, TypeError):
                logger.exception(
                    "Dataset preparation failed for %s",
                    dataset.dataset_id,
                )
                return _failure(
                    selected,
                    reason=PreparationFailureReason.VALIDATION_FAILED,
                    message="Deterministic normalization validation failed.",
                    code=IssueCode.PREPARATION_VALIDATION_FAILED,
                )
            write = NormalizedDatasetWrite(
                reference=reference,
                rows=transformed.rows,
                lineage=tuple(asdict(item) for item in transformed.lineage),
                excluded_rows=tuple(
                    asdict(item) for item in transformed.excluded_rows
                ),
                footnotes=tuple(
                    asdict(item) for item in transformed.footnotes
                ),
            )
            try:
                await self._normalized_repository.save(
                    user_id=user_id,
                    value=write,
                )
            except NormalizedDatasetRepositoryError:
                logger.exception(
                    "Prepared dataset persistence failed for %s",
                    dataset.dataset_id,
                )
                if (
                    recipe.materialization
                    == MaterializationType.SOURCE_PASSTHROUGH
                ):
                    warning = AnalysisIssue(
                        code=IssueCode.PREPARATION_CACHE_WRITE_FAILED,
                        severity=IssueSeverity.WARNING,
                        stage=IssueStage.PREPARATION,
                        message=(
                            "Passthrough preparation metadata could not be cached."
                        ),
                        retryable=True,
                        dataset_id=dataset.dataset_id,
                        table_id=dataset.table_id,
                        document_id=dataset.document_id,
                    )
                    return _PreparedWork(
                        dataset_id=dataset.dataset_id,
                        reference=reference,
                        warnings=(warning,),
                    )
                return _failure(
                    selected,
                    reason=PreparationFailureReason.PERSISTENCE_FAILED,
                    message="Normalized rows could not be persisted.",
                    code=IssueCode.PREPARATION_CACHE_WRITE_FAILED,
                    retryable=True,
                )
            return _PreparedWork(
                dataset_id=dataset.dataset_id,
                reference=reference,
            )

        async def prepare_one(selected: SelectedDataset) -> _PreparedWork:
            started = perf_counter()
            value = await prepare_one_unmeasured(selected)
            return replace(
                value,
                duration_ms=round((perf_counter() - started) * 1000, 3),
            )

        work = await asyncio.gather(
            *(prepare_one(item) for item in selection.datasets)
        )
        references = tuple(
            item.reference for item in work if item.reference is not None
        )
        failures = tuple(
            item.failure for item in work if item.failure is not None
        )
        errors.extend(item.issue for item in work if item.issue is not None)
        warnings.extend(
            warning for item in work for warning in item.warnings
        )
        status = (
            NormalizationStatus.READY
            if not failures
            else NormalizationStatus.FAILED
            if not references
            else NormalizationStatus.PARTIAL
        )
        artifact = NormalizationResult(
            run_id=run_id,
            input_evidence_signature=signature,
            status=status,
            datasets=references,
            selected_fact_ids=fact_ids,
            selected_derived_dataset_ids=derived_dataset_ids,
            non_tabular_requirement_ids=selection.non_tabular_requirement_ids,
            rejected_dataset_ids=selection.rejected_dataset_ids,
            failures=failures,
            selected_dataset_count=len(selection.datasets),
            prepared_dataset_count=len(references),
            cache_hit_count=sum(item.cache_hit for item in references),
            passthrough_count=sum(
                item.materialization
                == MaterializationType.SOURCE_PASSTHROUGH
                for item in references
            ),
            materialized_count=sum(
                item.materialization
                == MaterializationType.MATERIALIZED_DATASET
                for item in references
            ),
            total_input_rows=sum(item.input_row_count for item in references),
            total_output_rows=sum(item.output_row_count for item in references),
            can_analyze=status == NormalizationStatus.READY,
        )
        return PreparationRunOutcome(
            artifact=artifact,
            warnings=tuple(warnings),
            errors=tuple(errors),
            total_latency_ms=round(
                (perf_counter() - run_started) * 1000,
                3,
            ),
            dataset_latencies_ms=tuple(item.duration_ms for item in work),
        )
