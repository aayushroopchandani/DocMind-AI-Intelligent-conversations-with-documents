from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from scripts.data_analysis_agent.analysis.models.preparation import (
    NormalizationResult,
    NormalizedDataType,
    NormalizedDatasetReference,
)
from scripts.data_analysis_agent.analysis.models.profile import (
    ColumnProfile,
    DatasetProfiles,
)
from scripts.data_analysis_agent.analysis.models.requirements import (
    AnalysisRequirements,
)

from ..models.datasets import (
    DatasetHandle,
    DerivedDatasetLocator,
    PdfTableLocator,
    SpreadsheetRangeLocator,
    UploadedFileLocator,
)
from ..models.plans import (
    PlanColumn,
    PlanDataType,
    PlanDatasetProvenance,
    PlanInputDataset,
    WorkbookVersionGuard,
    compute_input_signature,
)
from ..models.runs import AnalysisMode, AnalysisRun
from .contracts import ExecutorCapabilities, PlanResourcePolicy


_CURRENCY_CODES = frozenset(
    {
        "aed",
        "aud",
        "brl",
        "cad",
        "chf",
        "cny",
        "eur",
        "gbp",
        "hkd",
        "inr",
        "jpy",
        "krw",
        "mxn",
        "nzd",
        "rub",
        "sek",
        "sgd",
        "usd",
        "zar",
    }
)
_CURRENCY_SYMBOLS = frozenset({"$", "€", "£", "¥", "₹"})


class PlanningColumnSummary(BaseModel):
    key: str
    label: str
    data_type: PlanDataType
    unit: str | None = None
    semantic_role: str
    missing_percentage: float = Field(ge=0, le=100)
    unique_count: int = Field(ge=0)
    example_values: tuple[
        Annotated[str, Field(max_length=240)], ...
    ] = Field(default=(), max_length=3)
    minimum: float | None = None
    maximum: float | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanningDatasetSummary(BaseModel):
    alias: str
    dataset_id: str
    title: str
    row_count: int = Field(ge=0)
    quality_score: float | None = Field(default=None, ge=0, le=1)
    orientation: str | None = None
    transformations: tuple[str, ...] = Field(default=(), max_length=24)
    columns: tuple[PlanningColumnSummary, ...] = Field(
        min_length=1,
        max_length=500,
    )

    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanningContext(BaseModel):
    """Bounded, row-free context supplied to the planning model."""

    run_id: str
    user_id: str
    workspace_id: str
    mode: AnalysisMode
    prompt: str
    input_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    requirements: AnalysisRequirements
    input_datasets: tuple[PlanInputDataset, ...] = Field(
        min_length=1,
        max_length=30,
    )
    dataset_summaries: tuple[PlanningDatasetSummary, ...] = Field(
        min_length=1,
        max_length=30,
    )
    workbook_guards: tuple[WorkbookVersionGuard, ...] = Field(
        default=(),
        max_length=24,
    )
    capabilities: ExecutorCapabilities
    resource_policy: PlanResourcePolicy

    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanningContextError(ValueError):
    """Phase 7 artifacts cannot form a trustworthy planning context."""


class PlanningContextBuilder:
    def __init__(
        self,
        *,
        capabilities: ExecutorCapabilities | None = None,
        resource_policy: PlanResourcePolicy | None = None,
    ) -> None:
        self._capabilities = capabilities or ExecutorCapabilities()
        self._resource_policy = resource_policy or PlanResourcePolicy()

    def build(
        self,
        *,
        run: AnalysisRun,
        dataset_handles: tuple[DatasetHandle, ...],
        requirements: AnalysisRequirements,
        profiles: DatasetProfiles,
        normalization: NormalizationResult,
    ) -> PlanningContext:
        if normalization.run_id != run.run_id:
            raise PlanningContextError("normalization belongs to another run")
        if not normalization.can_analyze or not normalization.datasets:
            raise PlanningContextError("normalization is not ready for planning")
        handles = {handle.dataset_id: handle for handle in dataset_handles}
        profile_by_dataset = {
            profile.dataset_id: profile for profile in profiles.profiles
        }
        inputs: list[PlanInputDataset] = []
        summaries: list[PlanningDatasetSummary] = []
        guards: dict[str, WorkbookVersionGuard] = {}
        for index, normalized in enumerate(normalization.datasets, start=1):
            for dataset_id, source_version in zip(
                normalized.source_dataset_ids,
                normalized.source_versions,
                strict=True,
            ):
                handle = handles.get(dataset_id)
                if (
                    handle is None
                    or handle.source_version != source_version
                    or handle.user_id != run.user_id
                    or handle.workspace_id != run.workspace_id
                ):
                    raise PlanningContextError(
                        "normalized dataset source is unavailable in this workspace"
                    )
            input_dataset = self._input_dataset(
                alias=f"input_{index}",
                normalized=normalized,
                handles=handles,
            )
            inputs.append(input_dataset)
            summaries.append(
                self._dataset_summary(
                    input_dataset=input_dataset,
                    normalized=normalized,
                    profile_by_dataset=profile_by_dataset,
                )
            )
            for source in input_dataset.provenance:
                if source.workbook_id is None:
                    continue
                assert source.workbook_revision is not None
                assert source.worksheet_id is not None
                assert source.snapshot_hash is not None
                guard = WorkbookVersionGuard(
                    workbook_id=source.workbook_id,
                    worksheet_id=source.worksheet_id,
                    workbook_revision=source.workbook_revision,
                    snapshot_hash=source.snapshot_hash,
                )
                existing = guards.get(guard.target_key)
                if existing is not None and existing != guard:
                    raise PlanningContextError(
                        "one workbook target has conflicting immutable versions"
                    )
                guards[guard.target_key] = guard
        input_tuple = tuple(inputs)
        context = PlanningContext(
            run_id=run.run_id,
            user_id=run.user_id,
            workspace_id=run.workspace_id,
            mode=run.mode,
            prompt=run.prompt,
            input_signature=compute_input_signature(input_tuple),
            requirements=requirements,
            input_datasets=input_tuple,
            dataset_summaries=tuple(summaries),
            workbook_guards=tuple(guards.values()),
            capabilities=self._capabilities,
            resource_policy=self._resource_policy,
        )
        if len(context.model_dump_json().encode("utf-8")) > (
            self._resource_policy.max_context_bytes
        ):
            raise PlanningContextError(
                "planning metadata exceeds the configured context limit"
            )
        return context

    @staticmethod
    def _input_dataset(
        *,
        alias: str,
        normalized: NormalizedDatasetReference,
        handles: dict[str, DatasetHandle],
    ) -> PlanInputDataset:
        provenance: list[PlanDatasetProvenance] = []
        for dataset_id, source_version in zip(
            normalized.source_dataset_ids,
            normalized.source_versions,
            strict=True,
        ):
            handle = handles.get(dataset_id)
            if handle is None or handle.source_version != source_version:
                raise PlanningContextError(
                    "normalized dataset references an unavailable source version"
                )
            provenance.append(_provenance(handle))
        columns = tuple(
            PlanColumn(
                key=column.key,
                label=column.label,
                data_type=_plan_data_type(column.data_type, column.unit),
                unit=column.unit,
                nullable=True,
            )
            for column in normalized.columns
        )
        return PlanInputDataset(
            alias=alias,
            dataset_id=normalized.normalized_dataset_id,
            dataset_version=normalized.recipe_hash,
            title=normalized.title,
            row_count=normalized.output_row_count,
            columns=columns,
            provenance=tuple(provenance),
        )

    @staticmethod
    def _dataset_summary(
        *,
        input_dataset: PlanInputDataset,
        normalized: NormalizedDatasetReference,
        profile_by_dataset: dict[str, object],
    ) -> PlanningDatasetSummary:
        source_profiles = [
            profile_by_dataset[dataset_id]
            for dataset_id in normalized.source_dataset_ids
            if dataset_id in profile_by_dataset
        ]
        profile_columns: dict[str, ColumnProfile] = {}
        for profile in source_profiles:
            columns = getattr(profile, "columns", ())
            for column in columns:
                profile_columns.setdefault(column.key, column)
        column_summaries: list[PlanningColumnSummary] = []
        for column in input_dataset.columns:
            profile = profile_columns.get(column.key)
            numeric = profile.numeric_statistics if profile is not None else None
            column_summaries.append(
                PlanningColumnSummary(
                    key=column.key,
                    label=column.label,
                    data_type=column.data_type,
                    unit=column.unit,
                    semantic_role=(
                        profile.semantic_role.value
                        if profile is not None
                        else "unknown"
                    ),
                    missing_percentage=(
                        profile.missing_percentage if profile is not None else 0
                    ),
                    unique_count=(profile.unique_count if profile is not None else 0),
                    example_values=(
                        tuple(
                            " ".join(value.split())[:240]
                            for value in profile.example_values
                        )
                        if profile is not None
                        else ()
                    ),
                    minimum=numeric.minimum if numeric is not None else None,
                    maximum=numeric.maximum if numeric is not None else None,
                )
            )
        return PlanningDatasetSummary(
            alias=input_dataset.alias,
            dataset_id=input_dataset.dataset_id,
            title=input_dataset.title,
            row_count=input_dataset.row_count,
            quality_score=(
                min(profile.quality_score for profile in source_profiles)
                if source_profiles
                else None
            ),
            orientation=(
                source_profiles[0].orientation.value if source_profiles else None
            ),
            transformations=tuple(
                transformation.operation.value
                for transformation in normalized.transformations
            ),
            columns=tuple(column_summaries),
        )


def _plan_data_type(
    data_type: NormalizedDataType,
    unit: str | None,
) -> PlanDataType:
    normalized_unit = (unit or "").strip().casefold()
    if normalized_unit in {"%", "percent", "percentage"}:
        return PlanDataType.PERCENTAGE
    unit_tokens = frozenset(
        normalized_unit.replace("/", " ").replace("-", " ").split()
    )
    if unit_tokens.intersection(_CURRENCY_CODES) or any(
        symbol in normalized_unit for symbol in _CURRENCY_SYMBOLS
    ):
        return PlanDataType.CURRENCY
    return {
        NormalizedDataType.STRING: PlanDataType.STRING,
        NormalizedDataType.DECIMAL: PlanDataType.DECIMAL,
        NormalizedDataType.INTEGER: PlanDataType.INTEGER,
        NormalizedDataType.BOOLEAN: PlanDataType.BOOLEAN,
        NormalizedDataType.DATE: PlanDataType.DATE,
        NormalizedDataType.PERIOD: PlanDataType.PERIOD,
        NormalizedDataType.UNKNOWN: PlanDataType.UNKNOWN,
    }[data_type]


def _provenance(handle: DatasetHandle) -> PlanDatasetProvenance:
    locator = handle.locator
    common = {
        "source_dataset_id": handle.dataset_id,
        "source_version": handle.source_version,
        "source_type": handle.source_type,
    }
    if isinstance(locator, PdfTableLocator):
        return PlanDatasetProvenance(
            **common,
            document_id=locator.document_id,
            page_start=locator.page_start,
            page_end=locator.page_end,
        )
    if isinstance(locator, SpreadsheetRangeLocator):
        return PlanDatasetProvenance(
            **common,
            artifact_id=locator.artifact_id,
            artifact_version_id=locator.artifact_version_id,
            workbook_id=locator.workbook_id,
            workbook_revision=locator.workbook_revision,
            worksheet_id=locator.worksheet_id,
            range_a1=locator.range_a1,
            snapshot_hash=locator.snapshot_hash,
        )
    if isinstance(locator, UploadedFileLocator):
        return PlanDatasetProvenance(
            **common,
            artifact_id=locator.artifact_id,
            artifact_version_id=locator.artifact_version_id,
            range_a1=locator.range_a1,
        )
    if isinstance(locator, DerivedDatasetLocator):
        return PlanDatasetProvenance(**common)
    raise PlanningContextError("dataset has unsupported provenance")


__all__ = [
    "PlanningColumnSummary",
    "PlanningContext",
    "PlanningContextBuilder",
    "PlanningContextError",
    "PlanningDatasetSummary",
]
