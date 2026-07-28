from __future__ import annotations

import unittest
from copy import deepcopy
from typing import Any

from db.models.structured_table import StructuredTable
from scripts.data_analysis_agent.analysis.models import (
    AnalysisOperation,
    AnalysisRequirements,
    AugmentedEvidence,
    CompletionStatus,
    CoverageStatus,
    DatasetAccessReference,
    DatasetColumn,
    DatasetProfiles,
    DocumentCoverage,
    DerivedDatasetColumn,
    DerivedDatasetReference,
    EvidenceAssessment,
    EvidenceFact,
    EvidenceKind,
    EvidencePackage,
    EvidenceReference,
    ExpectedDataType,
    HydratedDatasetReference,
    MatchMethod,
    MaterializationType,
    NormalizedDataType,
    NormalizationStatus,
    ReadinessDecision,
    RequirementCoverage,
    RequirementItem,
    RequirementKind,
    RequirementOrigin,
    SourceRegion,
    TableOrientation,
)
from scripts.data_analysis_agent.analysis.repositories import (
    NormalizedDatasetWrite,
)
from scripts.data_analysis_agent.analysis.services.preparation import (
    DatasetPreparationRunner,
    DeterministicDatasetTransformer,
    build_cleaning_recipe,
    select_preparation_evidence,
)
from scripts.data_analysis_agent.analysis.services.profiling import (
    DeterministicDatasetProfiler,
)
from scripts.data_analysis_agent.analysis.services.versioning import (
    raw_dataset_id,
    source_version,
)


DOCUMENT_ID = "d" * 64


def _raw_table(
    table_id: str,
    *,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    title: str = "Prepared source",
) -> dict[str, Any]:
    return {
        "table_id": table_id,
        "document_id": DOCUMENT_ID,
        "user_id": "user-1",
        "page_start": 7,
        "page_end": 7,
        "title": title,
        "extraction_method": "pymupdf",
        "columns": columns,
        "rows": rows,
        "source_fragments": [
            {"page": 7, "bounding_box": [10.0, 20.0, 500.0, 700.0]}
        ],
    }


def _dataset_and_profile(
    raw: dict[str, Any],
) -> tuple[HydratedDatasetReference, Any]:
    table = StructuredTable.model_validate(raw)
    version = source_version(table)
    dataset = HydratedDatasetReference(
        dataset_id=raw_dataset_id(table, version),
        source_version=version,
        table_id=table.table_id,
        document_id=table.document_id,
        document_name="report.pdf",
        title=table.title,
        page_start=table.page_start,
        page_end=table.page_end,
        extraction_method=table.extraction_method,
        columns=tuple(
            DatasetColumn(
                key=item.key,
                label=item.label,
                type=item.type,
                unit=item.unit,
            )
            for item in table.columns
        ),
        row_count=len(table.rows),
        source_regions=tuple(
            SourceRegion(
                page=item.page,
                bounding_box=tuple(item.bounding_box),
            )
            for item in table.source_fragments
        ),
        access=DatasetAccessReference(table_id=table.table_id),
        usable_for_analysis=True,
    )
    profile = DeterministicDatasetProfiler().profile(dataset, table)
    return dataset, profile


def _requirements(*, with_period: bool = False) -> AnalysisRequirements:
    values = [
        RequirementItem(
            requirement_id="req_metric_revenue",
            kind=RequirementKind.METRIC,
            name="revenue",
            expected_data_type=ExpectedDataType.NUMBER,
            origin=RequirementOrigin.EXPLICIT_GUARD,
        )
    ]
    if with_period:
        values.append(
            RequirementItem(
                requirement_id="req_period_2023",
                kind=RequirementKind.PERIOD,
                name="2023",
                expected_data_type=ExpectedDataType.DATE,
                origin=RequirementOrigin.EXPLICIT_GUARD,
            )
        )
    return AnalysisRequirements(
        model="test-model",
        operation=AnalysisOperation.LOOKUP,
        selected_document_ids=(DOCUMENT_ID,),
        requirements=tuple(values),
        table_evidence_required=True,
    )


def _assessment(
    dataset: HydratedDatasetReference,
    *,
    with_period: bool = False,
) -> EvidenceAssessment:
    coverage = [
        RequirementCoverage(
            requirement_id="req_metric_revenue",
            status=CoverageStatus.SUPPORTED,
            confidence=1,
            reason="Exact source match.",
            evidence=(
                EvidenceReference(
                    evidence_kind=EvidenceKind.DATASET_COLUMN,
                    dataset_id=dataset.dataset_id,
                    source_version=dataset.source_version,
                    table_id=dataset.table_id,
                    document_id=dataset.document_id,
                    column_key=dataset.columns[-1].key,
                    label="Revenue",
                    confidence=1,
                    match_method=MatchMethod.EXACT,
                ),
            ),
        )
    ]
    if with_period:
        coverage.append(
            RequirementCoverage(
                requirement_id="req_period_2023",
                status=CoverageStatus.SUPPORTED,
                confidence=1,
                reason="Period header match.",
                evidence=(
                    EvidenceReference(
                        evidence_kind=EvidenceKind.DATASET,
                        dataset_id=dataset.dataset_id,
                        source_version=dataset.source_version,
                        table_id=dataset.table_id,
                        document_id=dataset.document_id,
                        label="2023",
                        confidence=1,
                        match_method=MatchMethod.PROFILE_PERIOD,
                    ),
                ),
            )
        )
    return EvidenceAssessment(
        ambiguity_model="test",
        decision=ReadinessDecision.READY,
        coverage=tuple(coverage),
        document_coverage=(
            DocumentCoverage(
                document_id=DOCUMENT_ID,
                required=True,
                status=CoverageStatus.SUPPORTED,
                dataset_ids=(dataset.dataset_id,),
            ),
        ),
        required_count=len(coverage),
        supported_count=len(coverage),
        partial_count=0,
        missing_count=0,
        conflicting_count=0,
        ambiguous_count=0,
    )


def _profiles(profile: Any) -> DatasetProfiles:
    return DatasetProfiles(
        profiler_version=profile.profiler_version,
        status="complete",
        profiles=(profile,),
        requested_count=1,
        profiled_count=1,
        cache_hit_count=0,
        generated_count=1,
    )


def _evidence(dataset: HydratedDatasetReference) -> EvidencePackage:
    return EvidencePackage(
        run_id="run-1",
        status="complete",
        datasets=(dataset,),
        retrieved_table_count=1,
        hydrated_table_count=1,
    )


class _DatasetRepository:
    def __init__(self, *tables: dict[str, Any]) -> None:
        self.tables = tables
        self.calls = 0

    async def load_tables(self, **_kwargs: Any) -> tuple[dict[str, Any], ...]:
        self.calls += 1
        return self.tables


class _NormalizedRepository:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.writes: list[NormalizedDatasetWrite] = []

    async def load_many(
        self,
        *,
        user_id: str,
        cache_keys: tuple[str, ...],
    ) -> dict[str, Any]:
        return {
            key: self.values[(user_id, key)]
            for key in cache_keys
            if (user_id, key) in self.values
        }

    async def save(
        self,
        *,
        user_id: str,
        value: NormalizedDatasetWrite,
    ) -> Any:
        self.writes.append(value)
        self.values[(user_id, value.reference.cache_key)] = value.reference
        return value.reference


class DatasetPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_clean_key_value_table_uses_cached_source_passthrough(
        self,
    ) -> None:
        raw = _raw_table(
            "clean-table",
            columns=[
                {"key": "metric", "label": "Metric", "type": "string"},
                {"key": "value", "label": "Value", "type": "number"},
            ],
            rows=[
                {"metric": "Revenue", "value": 165.8},
                {"metric": "Profit", "value": 12.5},
            ],
        )
        dataset, profile = _dataset_and_profile(raw)
        repository = _NormalizedRepository()
        source = _DatasetRepository(raw)
        runner = DatasetPreparationRunner(
            dataset_repository=source,
            normalized_repository=repository,
        )
        kwargs = {
            "run_id": "run-1",
            "user_id": "user-1",
            "document_ids": (DOCUMENT_ID,),
            "requirements": _requirements(),
            "assessment": _assessment(dataset),
            "evidence": _evidence(dataset),
            "profiles": _profiles(profile),
        }

        cold = await runner.run(**kwargs)
        warm = await runner.run(**kwargs)

        self.assertEqual(cold.artifact.status, NormalizationStatus.READY)
        self.assertEqual(cold.artifact.passthrough_count, 1)
        self.assertEqual(cold.artifact.materialized_count, 0)
        self.assertEqual(
            cold.artifact.datasets[0].materialization,
            MaterializationType.SOURCE_PASSTHROUGH,
        )
        self.assertEqual(cold.artifact.datasets[0].access.record_id, "clean-table")
        self.assertEqual(repository.writes[0].rows, ())
        self.assertEqual(warm.artifact.cache_hit_count, 1)
        self.assertEqual(len(repository.writes), 1)
        self.assertEqual(source.calls, 2)

    async def test_wide_table_is_reshaped_with_row_and_cell_lineage(self) -> None:
        raw = _raw_table(
            "wide-table",
            columns=[
                {"key": "metric", "label": "Metric", "type": "string"},
                {"key": "y2022", "label": "2022", "type": "number"},
                {"key": "y2023", "label": "2023", "type": "number"},
            ],
            rows=[
                {"metric": "Revenue", "y2022": 148.5, "y2023": 165.8},
                {"metric": "Profit", "y2022": 10, "y2023": 12},
            ],
        )
        dataset, profile = _dataset_and_profile(raw)
        repository = _NormalizedRepository()
        outcome = await DatasetPreparationRunner(
            dataset_repository=_DatasetRepository(raw),
            normalized_repository=repository,
        ).run(
            run_id="run-wide",
            user_id="user-1",
            document_ids=(DOCUMENT_ID,),
            requirements=_requirements(with_period=True),
            assessment=_assessment(dataset, with_period=True),
            evidence=_evidence(dataset),
            profiles=_profiles(profile),
        )

        reference = outcome.artifact.datasets[0]
        write = repository.writes[0]
        self.assertEqual(
            reference.materialization,
            MaterializationType.MATERIALIZED_DATASET,
        )
        self.assertEqual(reference.input_row_count, 2)
        self.assertEqual(reference.output_row_count, 4)
        self.assertEqual(len(write.rows), 4)
        self.assertEqual(len(write.lineage), 4)
        self.assertEqual(
            {row["__period"] for row in write.rows},
            {"2022", "2023"},
        )
        self.assertEqual(
            {row["__value"] for row in write.rows},
            {"148.5", "165.8", "10", "12"},
        )
        self.assertEqual(
            {item["source_column_key"] for item in write.lineage},
            {"y2022", "y2023"},
        )
        self.assertEqual(
            {item["source_page"] for item in write.lineage},
            {7},
        )

    async def test_wide_reshape_preserves_total_row_classification(self) -> None:
        raw = _raw_table(
            "wide-total-table",
            columns=[
                {"key": "metric", "label": "Metric", "type": "string"},
                {"key": "y2022", "label": "2022", "type": "number"},
                {"key": "y2023", "label": "2023", "type": "number"},
            ],
            rows=[
                {"metric": "Revenue", "y2022": 100, "y2023": 120},
                {"metric": "Total", "y2022": 100, "y2023": 120},
            ],
        )
        dataset, profile = _dataset_and_profile(raw)
        repository = _NormalizedRepository()

        outcome = await DatasetPreparationRunner(
            dataset_repository=_DatasetRepository(raw),
            normalized_repository=repository,
        ).run(
            run_id="run-wide-total",
            user_id="user-1",
            document_ids=(DOCUMENT_ID,),
            requirements=_requirements(with_period=True),
            assessment=_assessment(dataset, with_period=True),
            evidence=_evidence(dataset),
            profiles=_profiles(profile),
        )

        reference = outcome.artifact.datasets[0]
        rows = repository.writes[0].rows
        self.assertEqual(reference.total_or_subtotal_row_count, 1)
        self.assertEqual(reference.output_row_count, 4)
        self.assertEqual(
            [row["__row_type"] for row in rows].count("total"),
            2,
        )
        self.assertEqual(
            [row["__row_type"] for row in rows].count("observation"),
            2,
        )

    async def test_decimal_comma_and_magnitude_keep_scale_metadata(self) -> None:
        raw = _raw_table(
            "scaled-table",
            columns=[
                {"key": "metric", "label": "Metric", "type": "string"},
                {"key": "value", "label": "Value", "type": "number"},
            ],
            rows=[{"metric": "Managed acres", "value": "9,9 MILLION"}],
        )
        dataset, profile = _dataset_and_profile(raw)
        repository = _NormalizedRepository()

        outcome = await DatasetPreparationRunner(
            dataset_repository=_DatasetRepository(raw),
            normalized_repository=repository,
        ).run(
            run_id="run-scaled",
            user_id="user-1",
            document_ids=(DOCUMENT_ID,),
            requirements=_requirements(),
            assessment=_assessment(dataset),
            evidence=_evidence(dataset),
            profiles=_profiles(profile),
        )

        reference = outcome.artifact.datasets[0]
        self.assertEqual(
            reference.materialization,
            MaterializationType.MATERIALIZED_DATASET,
        )
        self.assertEqual(repository.writes[0].rows[0]["value"], "9.9")
        self.assertEqual(
            next(item for item in reference.columns if item.key == "value").unit,
            "million",
        )

    async def test_verbose_period_headers_reshape_without_dropping_censored_values(
        self,
    ) -> None:
        raw = _raw_table(
            "customer-period-table",
            columns=[
                {"key": "customer", "label": "Customer", "type": "string"},
                {
                    "key": "y2024",
                    "label": "Year Ended December 31, 2024",
                    "type": "string",
                    "unit": "%",
                },
                {
                    "key": "y2023",
                    "label": "Year Ended December 31, 2023",
                    "type": "string",
                    "unit": "%",
                },
                {
                    "key": "y2022",
                    "label": "Year Ended December 31, 2022",
                    "type": "string",
                    "unit": "%",
                },
            ],
            rows=[
                {"customer": "A", "y2024": 19, "y2023": 35, "y2022": 31},
                {"customer": "B", "y2024": 12, "y2023": "* %", "y2022": "* %"},
            ],
        )
        dataset, profile = _dataset_and_profile(raw)
        repository = _NormalizedRepository()

        outcome = await DatasetPreparationRunner(
            dataset_repository=_DatasetRepository(raw),
            normalized_repository=repository,
        ).run(
            run_id="run-customer-period",
            user_id="user-1",
            document_ids=(DOCUMENT_ID,),
            requirements=_requirements(with_period=True),
            assessment=_assessment(dataset, with_period=True),
            evidence=_evidence(dataset),
            profiles=_profiles(profile),
        )

        reference = outcome.artifact.datasets[0]
        rows = repository.writes[0].rows
        self.assertEqual(profile.orientation, TableOrientation.WIDE_TIME_SERIES)
        self.assertEqual(profile.footnote_like_row_count, 0)
        self.assertEqual(reference.footnote_row_count, 0)
        self.assertEqual(reference.output_row_count, 6)
        self.assertEqual(
            {row["__period"] for row in rows},
            {"2022", "2023", "2024"},
        )
        self.assertIn("* %", {row["__value"] for row in rows})
        value_column = next(
            item for item in reference.columns if item.key == "__value"
        )
        self.assertEqual(value_column.data_type, NormalizedDataType.UNKNOWN)

    async def test_wide_mixed_unit_rows_preserve_scale_and_percentage(self) -> None:
        raw = _raw_table(
            "rd-mixed-unit-table",
            title="Research and development by 2024, 2023, 2022",
            columns=[
                {
                    "key": "metric",
                    "label": "(Dollars in thousands)",
                    "type": "string",
                    "unit": "%",
                },
                {
                    "key": "y2024",
                    "label": "Year Ended December 31, 2024",
                    "type": "number",
                    "unit": "USD",
                },
                {
                    "key": "y2023",
                    "label": "Year Ended December 31, 2023",
                    "type": "number",
                    "unit": "USD",
                },
                {
                    "key": "y2022",
                    "label": "Year Ended December 31, 2022",
                    "type": "number",
                    "unit": "USD",
                },
            ],
            rows=[
                {"metric": "Research and development", "y2024": 53566, "y2023": 50736, "y2022": 56126},
                {"metric": "As a percentage of total revenues", "y2024": 30, "y2023": 31, "y2022": 38},
            ],
        )
        dataset, profile = _dataset_and_profile(raw)
        repository = _NormalizedRepository()

        outcome = await DatasetPreparationRunner(
            dataset_repository=_DatasetRepository(raw),
            normalized_repository=repository,
        ).run(
            run_id="run-rd-mixed-units",
            user_id="user-1",
            document_ids=(DOCUMENT_ID,),
            requirements=_requirements(with_period=True),
            assessment=_assessment(dataset, with_period=True),
            evidence=_evidence(dataset),
            profiles=_profiles(profile),
        )

        rows = repository.writes[0].rows
        amount_units = {
            row["__unit"]
            for row in rows
            if row["metric"] == "Research and development"
        }
        percentage_units = {
            row["__unit"]
            for row in rows
            if row["metric"] == "As a percentage of total revenues"
        }
        self.assertEqual(amount_units, {"USD thousand"})
        self.assertEqual(percentage_units, {"percent"})
        self.assertEqual(
            next(
                item
                for item in outcome.artifact.datasets[0].columns
                if item.key == "metric"
            ).unit,
            None,
        )

    async def test_scale_only_table_header_enriches_declared_currency(
        self,
    ) -> None:
        raw = _raw_table(
            "segment-income-table",
            title=(
                "Segment total revenues, gross profit, and net income "
                "(loss) for the periods presented (in thousands)"
            ),
            columns=[
                {"key": "metric", "label": "Metric", "type": "string"},
                {
                    "key": "y2024",
                    "label": "2024",
                    "type": "number",
                    "unit": "USD",
                },
                {
                    "key": "y2023",
                    "label": "2023",
                    "type": "number",
                    "unit": "USD",
                },
            ],
            rows=[
                {
                    "metric": "Net income (loss)",
                    "y2024": 4057,
                    "y2023": 3105,
                }
            ],
        )
        dataset, profile = _dataset_and_profile(raw)
        repository = _NormalizedRepository()

        await DatasetPreparationRunner(
            dataset_repository=_DatasetRepository(raw),
            normalized_repository=repository,
        ).run(
            run_id="run-scale-only-header",
            user_id="user-1",
            document_ids=(DOCUMENT_ID,),
            requirements=_requirements(with_period=True),
            assessment=_assessment(dataset, with_period=True),
            evidence=_evidence(dataset),
            profiles=_profiles(profile),
        )

        self.assertEqual(
            {row["__unit"] for row in repository.writes[0].rows},
            {"USD thousand"},
        )

    async def test_partially_temporal_dimension_is_typed_string_with_time_role(
        self,
    ) -> None:
        raw = _raw_table(
            "future-amortization",
            title="Year Ending December 31,",
            columns=[
                {
                    "key": "period",
                    "label": "Year Ending December 31,",
                    "type": "string",
                },
                {
                    "key": "amount",
                    "label": "Amount",
                    "type": "number",
                    "unit": "USD",
                },
            ],
            rows=[
                {"period": 2025, "amount": 3061},
                {"period": 2026, "amount": 2891},
                {"period": 2027, "amount": 2738},
                {"period": 2028, "amount": 2432},
                {"period": 2029, "amount": 747},
                {"period": "2030 and thereafter", "amount": 438},
                {"period": "Total future amortization expense", "amount": 12307},
            ],
        )
        dataset, profile = _dataset_and_profile(raw)
        repository = _NormalizedRepository()

        outcome = await DatasetPreparationRunner(
            dataset_repository=_DatasetRepository(raw),
            normalized_repository=repository,
        ).run(
            run_id="run-partial-period",
            user_id="user-1",
            document_ids=(DOCUMENT_ID,),
            requirements=_requirements(),
            assessment=_assessment(dataset),
            evidence=_evidence(dataset),
            profiles=_profiles(profile),
        )

        period_column = next(
            item
            for item in outcome.artifact.datasets[0].columns
            if item.key == "period"
        )
        self.assertEqual(period_column.data_type, NormalizedDataType.STRING)
        self.assertEqual(period_column.semantic_role, "time_period")

    async def test_source_version_change_returns_structured_failure(self) -> None:
        raw = _raw_table(
            "versioned-table",
            columns=[
                {"key": "metric", "label": "Metric", "type": "string"},
                {"key": "value", "label": "Value", "type": "number"},
            ],
            rows=[{"metric": "Revenue", "value": 100}],
        )
        dataset, profile = _dataset_and_profile(raw)
        changed = deepcopy(raw)
        changed["rows"][0]["value"] = 101
        repository = _NormalizedRepository()

        outcome = await DatasetPreparationRunner(
            dataset_repository=_DatasetRepository(changed),
            normalized_repository=repository,
        ).run(
            run_id="run-version",
            user_id="user-1",
            document_ids=(DOCUMENT_ID,),
            requirements=_requirements(),
            assessment=_assessment(dataset),
            evidence=_evidence(dataset),
            profiles=_profiles(profile),
        )

        self.assertEqual(outcome.artifact.status, NormalizationStatus.FAILED)
        self.assertFalse(outcome.artifact.can_analyze)
        self.assertEqual(
            outcome.artifact.failures[0].reason,
            "source_version_mismatch",
        )
        self.assertEqual(repository.writes, [])

    async def test_non_ready_assessment_cannot_enter_preparation(self) -> None:
        raw = _raw_table(
            "guarded-table",
            columns=[
                {"key": "metric", "label": "Metric", "type": "string"},
                {"key": "value", "label": "Value", "type": "number"},
            ],
            rows=[{"metric": "Revenue", "value": 100}],
        )
        dataset, profile = _dataset_and_profile(raw)
        assessment = _assessment(dataset).model_copy(
            update={"decision": ReadinessDecision.NEEDS_RETRIEVAL_REPAIR}
        )

        with self.assertRaisesRegex(ValueError, "final ready"):
            await DatasetPreparationRunner(
                dataset_repository=_DatasetRepository(raw),
                normalized_repository=_NormalizedRepository(),
            ).run(
                run_id="run-guard",
                user_id="user-1",
                document_ids=(DOCUMENT_ID,),
                requirements=_requirements(),
                assessment=assessment,
                evidence=_evidence(dataset),
                profiles=_profiles(profile),
            )

    async def test_validated_derived_series_is_selected_without_recleaning(
        self,
    ) -> None:
        fact = EvidenceFact(
            fact_id="fact_" + ("1" * 24),
            requirement_id="req_metric_revenue",
            entity="Example",
            metric="Revenue",
            raw_value="$100 million",
            normalized_value="100",
            unit="USD million",
            period="2023",
            document_id=DOCUMENT_ID,
            chunk_id="chunk-1",
            page=7,
            source_span="Revenue was $100 million in 2023.",
            span_start=0,
            span_end=33,
            chunk_hash="2" * 64,
            confidence=0.95,
            model="test-model",
        )
        derived = DerivedDatasetReference(
            derived_dataset_id="derived_" + ("3" * 24),
            document_id=DOCUMENT_ID,
            title="Revenue by period",
            summary="Example revenue by period.",
            source_chunk_ids=("chunk-1",),
            source_content_hashes=("2" * 64,),
            requirement_ids=("req_metric_revenue",),
            columns=(
                DerivedDatasetColumn(
                    key="period",
                    label="Period",
                    type="string",
                ),
                DerivedDatasetColumn(
                    key="value",
                    label="Value",
                    type="number",
                ),
            ),
            row_count=3,
            periods=("2021", "2022", "2023"),
            model="test-model",
        )
        augmented = AugmentedEvidence(
            run_id="run-derived",
            base_evidence_signature="4" * 64,
            status=CompletionStatus.READY,
            facts=(fact,),
            derived_datasets=(derived,),
            final_decision="ready",
        )
        assessment = EvidenceAssessment(
            ambiguity_model="test",
            decision=ReadinessDecision.READY,
            coverage=(
                RequirementCoverage(
                    requirement_id="req_metric_revenue",
                    status=CoverageStatus.SUPPORTED,
                    confidence=0.95,
                    reason="Validated extracted series.",
                    evidence=(
                        EvidenceReference(
                            evidence_kind=EvidenceKind.FACT,
                            document_id=DOCUMENT_ID,
                            chunk_id="chunk-1",
                            fact_id=fact.fact_id,
                            label="Revenue",
                            confidence=0.95,
                            match_method=MatchMethod.VALIDATED_EXTRACTION,
                        ),
                    ),
                ),
            ),
            document_coverage=(
                DocumentCoverage(
                    document_id=DOCUMENT_ID,
                    required=True,
                    status=CoverageStatus.SUPPORTED,
                    fact_ids=(fact.fact_id,),
                ),
            ),
            required_count=1,
            supported_count=1,
            partial_count=0,
            missing_count=0,
            conflicting_count=0,
            ambiguous_count=0,
        )
        source = _DatasetRepository()

        outcome = await DatasetPreparationRunner(
            dataset_repository=source,
            normalized_repository=_NormalizedRepository(),
        ).run(
            run_id="run-derived",
            user_id="user-1",
            document_ids=(DOCUMENT_ID,),
            requirements=_requirements(),
            assessment=assessment,
            evidence=EvidencePackage(
                run_id="run-derived",
                status="empty",
                retrieved_table_count=0,
                hydrated_table_count=0,
            ),
            profiles=DatasetProfiles(
                profiler_version="test",
                status="empty",
                requested_count=0,
                profiled_count=0,
                cache_hit_count=0,
                generated_count=0,
            ),
            augmented=augmented,
        )

        self.assertEqual(outcome.artifact.status, NormalizationStatus.READY)
        self.assertEqual(
            outcome.artifact.selected_derived_dataset_ids,
            (derived.derived_dataset_id,),
        )
        self.assertEqual(outcome.artifact.selected_fact_ids, ())
        self.assertEqual(source.calls, 0)

    async def test_headers_duplicates_totals_and_footnotes_are_lossless(
        self,
    ) -> None:
        raw = _raw_table(
            "messy-table",
            columns=[
                {"key": "metric", "label": "Metric", "type": "string"},
                {
                    "key": "value",
                    "label": "Value (USD million)",
                    "type": "number",
                },
            ],
            rows=[
                {"metric": "Metric", "value": "Value (USD million)"},
                {"metric": "Revenue", "value": "165.8"},
                {"metric": "Revenue", "value": "165.8"},
                {"metric": "Total", "value": "165.8"},
                {"metric": "Note: amounts in USD million", "value": None},
            ],
        )
        dataset, profile = _dataset_and_profile(raw)
        repository = _NormalizedRepository()
        outcome = await DatasetPreparationRunner(
            dataset_repository=_DatasetRepository(raw),
            normalized_repository=repository,
        ).run(
            run_id="run-messy",
            user_id="user-1",
            document_ids=(DOCUMENT_ID,),
            requirements=_requirements(),
            assessment=_assessment(dataset),
            evidence=_evidence(dataset),
            profiles=_profiles(profile),
        )

        reference = outcome.artifact.datasets[0]
        write = repository.writes[0]
        self.assertEqual(reference.input_row_count, 5)
        self.assertEqual(reference.retained_source_row_count, 2)
        self.assertEqual(reference.output_row_count, 2)
        self.assertEqual(reference.duplicate_row_count, 1)
        self.assertEqual(reference.repeated_header_row_count, 1)
        self.assertEqual(reference.footnote_row_count, 1)
        self.assertEqual(reference.total_or_subtotal_row_count, 1)
        self.assertEqual(
            {row["__row_type"] for row in write.rows},
            {"observation", "total"},
        )
        self.assertEqual(write.footnotes[0]["note_type"], "unit")
        self.assertEqual(write.footnotes[0]["source_row_index"], 4)
        self.assertEqual(
            {item["source_row_index"] for item in write.excluded_rows},
            {0, 2, 4},
        )

    def test_selection_prefers_one_high_coverage_dataset(self) -> None:
        raw = _raw_table(
            "selection-table",
            columns=[
                {"key": "metric", "label": "Metric", "type": "string"},
                {"key": "value", "label": "Revenue", "type": "number"},
            ],
            rows=[{"metric": "Revenue", "value": 100}],
        )
        first, profile = _dataset_and_profile(raw)
        second_raw = {**raw, "table_id": "selection-table-2"}
        second, second_profile = _dataset_and_profile(second_raw)
        requirements = _requirements(with_period=True)
        assessment = EvidenceAssessment(
            ambiguity_model="test",
            decision=ReadinessDecision.READY,
            coverage=(
                RequirementCoverage(
                    requirement_id="req_metric_revenue",
                    status=CoverageStatus.SUPPORTED,
                    confidence=1,
                    reason="Supported.",
                    evidence=(
                        EvidenceReference(
                            evidence_kind=EvidenceKind.DATASET,
                            dataset_id=first.dataset_id,
                            source_version=first.source_version,
                            table_id=first.table_id,
                            document_id=DOCUMENT_ID,
                            label="Revenue",
                            confidence=1,
                            match_method=MatchMethod.EXACT,
                        ),
                        EvidenceReference(
                            evidence_kind=EvidenceKind.DATASET,
                            dataset_id=second.dataset_id,
                            source_version=second.source_version,
                            table_id=second.table_id,
                            document_id=DOCUMENT_ID,
                            label="Revenue",
                            confidence=0.8,
                            match_method=MatchMethod.LEXICAL,
                        ),
                    ),
                ),
                RequirementCoverage(
                    requirement_id="req_period_2023",
                    status=CoverageStatus.SUPPORTED,
                    confidence=1,
                    reason="Supported.",
                    evidence=(
                        EvidenceReference(
                            evidence_kind=EvidenceKind.DATASET,
                            dataset_id=first.dataset_id,
                            source_version=first.source_version,
                            table_id=first.table_id,
                            document_id=DOCUMENT_ID,
                            label="2023",
                            confidence=1,
                            match_method=MatchMethod.PROFILE_PERIOD,
                        ),
                    ),
                ),
            ),
            document_coverage=(
                DocumentCoverage(
                    document_id=DOCUMENT_ID,
                    required=True,
                    status=CoverageStatus.SUPPORTED,
                    dataset_ids=(first.dataset_id, second.dataset_id),
                ),
            ),
            required_count=2,
            supported_count=2,
            partial_count=0,
            missing_count=0,
            conflicting_count=0,
            ambiguous_count=0,
        )
        profiles = DatasetProfiles(
            profiler_version=profile.profiler_version,
            status="complete",
            profiles=(profile, second_profile),
            requested_count=2,
            profiled_count=2,
            cache_hit_count=0,
            generated_count=2,
        )
        evidence = EvidencePackage(
            run_id="run-1",
            status="complete",
            datasets=(first, second),
            retrieved_table_count=2,
            hydrated_table_count=2,
        )

        selection = select_preparation_evidence(
            requirements=requirements,
            assessment=assessment,
            evidence=evidence,
            profiles=profiles,
        )

        self.assertEqual(len(selection.datasets), 1)
        self.assertEqual(selection.datasets[0].dataset.dataset_id, first.dataset_id)
        self.assertEqual(selection.rejected_dataset_ids, (second.dataset_id,))


if __name__ == "__main__":
    unittest.main()
