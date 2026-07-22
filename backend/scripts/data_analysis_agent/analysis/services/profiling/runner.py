from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from pydantic import ValidationError

from db.models.structured_table import StructuredTable

from ...models import (
    DATASET_PROFILER_VERSION,
    AnalysisIssue,
    DatasetProfile,
    DatasetProfileFailure,
    DatasetProfiles,
    EvidencePackage,
    HydratedDatasetReference,
    IssueCode,
    IssueSeverity,
    IssueStage,
    ProfileFailureReason,
    profile_cache_key,
)
from ...repositories import (
    DatasetRepository,
    DatasetRepositoryError,
    ProfileCache,
    ProfileCacheError,
)
from ..versioning import source_version
from .profiler import DeterministicDatasetProfiler


logger = logging.getLogger(__name__)
DEFAULT_PROFILE_CONCURRENCY = 4
MAX_PROFILE_CONCURRENCY = 8


class DatasetProfiler(Protocol):
    version: str

    def profile(
        self,
        dataset: HydratedDatasetReference,
        table: StructuredTable,
    ) -> DatasetProfile: ...


@dataclass(frozen=True, slots=True)
class ProfilingRunOutcome:
    artifact: DatasetProfiles
    warnings: tuple[AnalysisIssue, ...] = ()
    errors: tuple[AnalysisIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class _WorkResult:
    dataset_id: str
    profile: DatasetProfile | None = None
    failure: DatasetProfileFailure | None = None
    warning: AnalysisIssue | None = None
    cache_hit: bool = False


def _failure(
    dataset: HydratedDatasetReference,
    *,
    reason: ProfileFailureReason,
    message: str,
    issue_code: IssueCode,
    retryable: bool = False,
) -> _WorkResult:
    return _WorkResult(
        dataset_id=dataset.dataset_id,
        failure=DatasetProfileFailure(
            dataset_id=dataset.dataset_id,
            source_version=dataset.source_version,
            table_id=dataset.table_id,
            document_id=dataset.document_id,
            reason=reason,
            message=message,
            retryable=retryable,
        ),
        warning=AnalysisIssue(
            code=issue_code,
            severity=IssueSeverity.WARNING,
            stage=IssueStage.PROFILING,
            message=message,
            retryable=retryable,
            dataset_id=dataset.dataset_id,
            table_id=dataset.table_id,
            document_id=dataset.document_id,
        ),
    )


def _artifact_status(requested: int, profiled: int) -> str:
    if requested == 0:
        return "empty"
    if profiled == requested:
        return "complete"
    if profiled == 0:
        return "failed"
    return "partial"


class DatasetProfilingRunner:
    """Coordinate cache, materialization, version checks, and isolated profiling."""

    def __init__(
        self,
        *,
        dataset_repository: DatasetRepository,
        profile_cache: ProfileCache,
        profiler: DatasetProfiler | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self._dataset_repository = dataset_repository
        self._profile_cache = profile_cache
        self._profiler = profiler or DeterministicDatasetProfiler()
        configured = max_concurrency
        if configured is None:
            try:
                configured = int(
                    os.getenv(
                        "DATA_ANALYSIS_PROFILE_CONCURRENCY",
                        str(DEFAULT_PROFILE_CONCURRENCY),
                    )
                )
            except ValueError:
                logger.warning(
                    "Invalid DATA_ANALYSIS_PROFILE_CONCURRENCY; using %d",
                    DEFAULT_PROFILE_CONCURRENCY,
                )
                configured = DEFAULT_PROFILE_CONCURRENCY
        self._max_concurrency = min(MAX_PROFILE_CONCURRENCY, max(1, configured))

    async def run(
        self,
        *,
        user_id: str,
        document_ids: Sequence[str],
        evidence: EvidencePackage,
    ) -> ProfilingRunOutcome:
        datasets = evidence.datasets
        if not datasets:
            return ProfilingRunOutcome(
                artifact=DatasetProfiles(
                    profiler_version=self._profiler.version,
                    status="empty",
                    requested_count=0,
                    profiled_count=0,
                    cache_hit_count=0,
                    generated_count=0,
                )
            )

        warnings: list[AnalysisIssue] = []
        errors: list[AnalysisIssue] = []
        keys_by_dataset = {
            dataset.dataset_id: profile_cache_key(
                dataset_id=dataset.dataset_id,
                source_version=dataset.source_version,
                profiler_version=self._profiler.version,
            )
            for dataset in datasets
        }
        cache_task = asyncio.create_task(
            self._profile_cache.load_many(
                user_id=user_id,
                cache_keys=tuple(keys_by_dataset.values()),
            )
        )
        tables_task = asyncio.create_task(
            self._dataset_repository.load_tables(
                user_id=user_id,
                document_ids=document_ids,
                table_ids=tuple(dataset.table_id for dataset in datasets),
            )
        )
        try:
            cached_by_key = await cache_task
        except ProfileCacheError:
            logger.exception("Dataset profile cache read failed")
            cached_by_key = {}
            warnings.append(
                AnalysisIssue(
                    code=IssueCode.PROFILE_CACHE_READ_FAILED,
                    severity=IssueSeverity.WARNING,
                    stage=IssueStage.PROFILING,
                    message="Cached dataset profiles could not be read.",
                    retryable=True,
                )
            )

        cached_candidates: dict[str, DatasetProfile] = {}
        for dataset in datasets:
            profile = cached_by_key.get(keys_by_dataset[dataset.dataset_id])
            if profile is not None and (
                profile.dataset_id == dataset.dataset_id
                and profile.source_version == dataset.source_version
                and profile.profiler_version == self._profiler.version
                and profile.table_id == dataset.table_id
                and profile.document_id == dataset.document_id
            ):
                cached_candidates[dataset.dataset_id] = profile

        work_results: dict[str, _WorkResult] = {}
        generated_profiles: list[DatasetProfile] = []
        try:
            raw_tables = await tables_task
        except DatasetRepositoryError:
            logger.exception("Dataset materialization failed")
            errors.append(
                AnalysisIssue(
                    code=IssueCode.PROFILE_DATA_LOAD_FAILED,
                    severity=IssueSeverity.ERROR,
                    stage=IssueStage.PROFILING,
                    message="Source rows could not be loaded for profiling.",
                    retryable=True,
                )
            )
            for dataset in datasets:
                work_results[dataset.dataset_id] = _failure(
                    dataset,
                    reason=ProfileFailureReason.NOT_AVAILABLE,
                    message="Source rows could not be loaded for this dataset.",
                    issue_code=IssueCode.DATASET_NOT_AVAILABLE,
                    retryable=True,
                )
        else:
            tables_by_id = {
                table_id: table
                for table in raw_tables
                if (table_id := str(table.get("table_id") or "").strip())
            }
            semaphore = asyncio.Semaphore(self._max_concurrency)
            allowed_document_ids = set(document_ids)

            async def profile_one(
                dataset: HydratedDatasetReference,
            ) -> _WorkResult:
                async with semaphore:
                    return await asyncio.to_thread(
                        self._profile_one,
                        user_id,
                        allowed_document_ids,
                        dataset,
                        tables_by_id.get(dataset.table_id),
                        cached_candidates.get(dataset.dataset_id),
                    )

            completed = await asyncio.gather(
                *(profile_one(dataset) for dataset in datasets)
            )
            work_results = {result.dataset_id: result for result in completed}
            generated_profiles = [
                result.profile
                for result in completed
                if result.profile is not None and not result.cache_hit
            ]
            warnings.extend(
                result.warning
                for result in completed
                if result.warning is not None
            )

        if generated_profiles:
            try:
                await self._profile_cache.save_many(
                    user_id=user_id,
                    profiles=generated_profiles,
                )
            except ProfileCacheError:
                logger.exception("Dataset profile cache write failed")
                warnings.append(
                    AnalysisIssue(
                        code=IssueCode.PROFILE_CACHE_WRITE_FAILED,
                        severity=IssueSeverity.WARNING,
                        stage=IssueStage.PROFILING,
                        message="Generated dataset profiles could not be cached.",
                        retryable=True,
                    )
                )

        ordered_profiles: list[DatasetProfile] = []
        ordered_failures: list[DatasetProfileFailure] = []
        for dataset in datasets:
            result = work_results[dataset.dataset_id]
            if result.profile is not None:
                ordered_profiles.append(result.profile)
            elif result.failure is not None:
                ordered_failures.append(result.failure)

        artifact = DatasetProfiles(
            profiler_version=self._profiler.version,
            status=_artifact_status(len(datasets), len(ordered_profiles)),
            profiles=tuple(ordered_profiles),
            failures=tuple(ordered_failures),
            requested_count=len(datasets),
            profiled_count=len(ordered_profiles),
            cache_hit_count=sum(result.cache_hit for result in work_results.values()),
            generated_count=len(generated_profiles),
        )
        return ProfilingRunOutcome(
            artifact=artifact,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )

    def _profile_one(
        self,
        user_id: str,
        allowed_document_ids: set[str],
        dataset: HydratedDatasetReference,
        raw_table: dict[str, Any] | None,
        cached_profile: DatasetProfile | None,
    ) -> _WorkResult:
        if raw_table is None:
            return _failure(
                dataset,
                reason=ProfileFailureReason.NOT_AVAILABLE,
                message="The hydrated dataset is no longer available.",
                issue_code=IssueCode.DATASET_NOT_AVAILABLE,
                retryable=True,
            )
        try:
            table = StructuredTable.model_validate(raw_table)
        except ValidationError:
            return _failure(
                dataset,
                reason=ProfileFailureReason.INVALID_TABLE,
                message="The dataset failed schema validation during profiling.",
                issue_code=IssueCode.DATASET_PROFILE_FAILED,
            )
        if table.user_id != user_id or table.document_id not in allowed_document_ids:
            return _failure(
                dataset,
                reason=ProfileFailureReason.NOT_AVAILABLE,
                message="The dataset is outside the authorized document scope.",
                issue_code=IssueCode.DATASET_NOT_AVAILABLE,
            )
        try:
            current_source_version = source_version(table)
        except Exception:
            logger.exception(
                "Dataset source versioning failed for %s", dataset.dataset_id
            )
            return _failure(
                dataset,
                reason=ProfileFailureReason.PROFILING_FAILED,
                message="The dataset source version could not be verified.",
                issue_code=IssueCode.DATASET_PROFILE_FAILED,
            )
        if current_source_version != dataset.source_version:
            return _failure(
                dataset,
                reason=ProfileFailureReason.SOURCE_VERSION_MISMATCH,
                message="The source table changed after evidence hydration.",
                issue_code=IssueCode.DATASET_VERSION_MISMATCH,
                retryable=True,
            )
        if cached_profile is not None:
            return _WorkResult(
                dataset_id=dataset.dataset_id,
                profile=cached_profile,
                cache_hit=True,
            )
        try:
            profile = self._profiler.profile(dataset, table)
        except Exception:
            logger.exception("Dataset profiling failed for %s", dataset.dataset_id)
            return _failure(
                dataset,
                reason=ProfileFailureReason.PROFILING_FAILED,
                message="The dataset could not be profiled.",
                issue_code=IssueCode.DATASET_PROFILE_FAILED,
            )
        return _WorkResult(dataset_id=dataset.dataset_id, profile=profile)


__all__ = [
    "DATASET_PROFILER_VERSION",
    "DatasetProfiler",
    "DatasetProfilingRunner",
    "ProfilingRunOutcome",
]
