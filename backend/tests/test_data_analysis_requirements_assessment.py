from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import patch

from db.models.structured_table import StructuredTable
from scripts.data_analysis_agent.analysis.models import (
    AnalysisOperation,
    AnalysisRequest,
    AnalysisRequirements,
    CoverageStatus,
    DatasetAccessReference,
    DatasetColumn,
    DatasetProfiles,
    DocumentCoverage,
    EvidenceAssessment,
    EvidenceKind,
    EvidencePackage,
    ExpectedDataType,
    ExtractedRequirement,
    FilterOperator,
    HydratedDatasetReference,
    IssueCode,
    ReadinessDecision,
    RequirementCoverage,
    RequirementItem,
    RequirementKind,
    RequirementsExtraction,
    RetrievalResult,
    RetrievalSignals,
    SourceRegion,
    TextEvidenceReference,
)
from scripts.data_analysis_agent.analysis.repositories import (
    MongoAssessmentCache,
    MongoAssessmentMetadataRepository,
    MongoRequirementsCache,
    TableAssessmentMetadata,
)
from scripts.data_analysis_agent.analysis.services import (
    AmbiguityResolution,
    AmbiguityResolutionBatch,
    AmbiguityResolver,
    AnalysisRequirementsRunner,
    EvidenceAssessmentRunner,
    RequirementsExtractor,
    validate_requirements_extraction,
)
from scripts.data_analysis_agent.analysis.services.profiling import (
    DeterministicDatasetProfiler,
)
from scripts.data_analysis_agent.analysis.services.versioning import (
    raw_dataset_id,
    source_version,
)


DOCUMENT_A = "a" * 64
DOCUMENT_B = "b" * 64


class _ArtifactCache:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.loads = 0
        self.saves = 0

    async def load(self, *, user_id: str, cache_key: str) -> Any:
        self.loads += 1
        return self.values.get(f"{user_id}:{cache_key}")

    async def save(
        self,
        *,
        user_id: str,
        cache_key: str,
        **kwargs: Any,
    ) -> None:
        self.saves += 1
        value = kwargs.get("requirements") or kwargs.get("assessment")
        self.values[f"{user_id}:{cache_key}"] = value


class _RequirementsGenerator:
    def __init__(
        self,
        extraction: RequirementsExtraction | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.extraction = extraction
        self.fail = fail
        self.calls = 0

    async def ainvoke(self, _input: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        if self.fail:
            raise RuntimeError("model unavailable")
        return self.extraction


class _MetadataRepository:
    def __init__(
        self,
        values: dict[str, TableAssessmentMetadata] | None = None,
    ) -> None:
        self.values = values or {}
        self.calls = 0

    async def load_table_metadata(self, **_kwargs: Any) -> Any:
        self.calls += 1
        return self.values


class _AmbiguityGenerator:
    def __init__(self, decision: str = "match") -> None:
        self.decision = decision
        self.calls = 0

    async def ainvoke(self, messages: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        payload = json.loads(messages[-1].content)
        return AmbiguityResolutionBatch(
            resolutions=tuple(
                AmbiguityResolution(
                    pair_id=item["pair_id"],
                    decision=self.decision,
                    confidence=0.92,
                    reason="The compact labels are semantically equivalent.",
                )
                for item in payload["pairs"]
            )
        )


class _MongoCursor:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        return self.values[:length]


class _MongoCollection:
    def __init__(self, values: list[dict[str, Any]] | None = None) -> None:
        self.values = values or []
        self.find_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.find_one_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.update_calls: list[tuple[dict[str, Any], dict[str, Any], bool]] = []

    def find(
        self,
        query: dict[str, Any],
        projection: dict[str, Any],
    ) -> _MongoCursor:
        self.find_calls.append((query, projection))
        return _MongoCursor(self.values)

    async def find_one(
        self,
        query: dict[str, Any],
        projection: dict[str, Any],
    ) -> dict[str, Any] | None:
        self.find_one_calls.append((query, projection))
        return self.values[0] if self.values else None

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool,
    ) -> None:
        self.update_calls.append((query, update, upsert))


def _request(
    query: str,
    *,
    document_ids: tuple[str, ...] = (DOCUMENT_A,),
) -> AnalysisRequest:
    return AnalysisRequest(
        user_id="user-1",
        chat_id="chat-1",
        query=query,
        document_ids=document_ids,
    )


def _raw_table(
    *,
    table_id: str,
    document_id: str,
    title: str,
    document_name: str,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    summary: str = "",
) -> tuple[HydratedDatasetReference, Any, TableAssessmentMetadata]:
    raw = {
        "table_id": table_id,
        "document_id": document_id,
        "user_id": "user-1",
        "page_start": 2,
        "page_end": 2,
        "title": title,
        "extraction_method": "pymupdf",
        "columns": columns,
        "rows": rows,
        "source_fragments": [
            {"page": 2, "bounding_box": [10.0, 20.0, 500.0, 700.0]}
        ],
        "summary": summary,
    }
    table = StructuredTable.model_validate(raw)
    version = source_version(table)
    dataset = HydratedDatasetReference(
        dataset_id=raw_dataset_id(table, version),
        source_version=version,
        table_id=table.table_id,
        document_id=table.document_id,
        document_name=document_name,
        title=table.title,
        page_start=table.page_start,
        page_end=table.page_end,
        extraction_method=table.extraction_method,
        columns=tuple(
            DatasetColumn(
                key=column.key,
                label=column.label,
                type=column.type,
                unit=column.unit,
            )
            for column in table.columns
        ),
        row_count=len(table.rows),
        source_regions=(
            SourceRegion(
                page=2,
                bounding_box=(10.0, 20.0, 500.0, 700.0),
            ),
        ),
        access=DatasetAccessReference(table_id=table.table_id),
        usable_for_analysis=bool(table.rows),
    )
    profile = DeterministicDatasetProfiler().profile(dataset, table)
    metadata = TableAssessmentMetadata(
        table_id=table.table_id,
        document_id=document_id,
        title=title,
        summary=summary,
        keywords=(),
    )
    return dataset, profile, metadata


def _evidence_and_profiles(
    *items: tuple[HydratedDatasetReference, Any, TableAssessmentMetadata],
) -> tuple[EvidencePackage, DatasetProfiles, dict[str, TableAssessmentMetadata]]:
    datasets = tuple(item[0] for item in items)
    profiles = tuple(item[1] for item in items)
    return (
        EvidencePackage(
            run_id="run-1",
            status="complete" if datasets else "empty",
            datasets=datasets,
            retrieved_table_count=len(datasets),
            hydrated_table_count=len(datasets),
        ),
        DatasetProfiles(
            profiler_version=(
                profiles[0].profiler_version if profiles else "1.0.0"
            ),
            status="complete" if profiles else "empty",
            profiles=profiles,
            requested_count=len(profiles),
            profiled_count=len(profiles),
            cache_hit_count=0,
            generated_count=len(profiles),
        ),
        {item[2].table_id: item[2] for item in items},
    )


def _retrieval(*chunks: TextEvidenceReference) -> RetrievalResult:
    return RetrievalResult(
        retrieval_scope="normal",
        table_intent="required",
        signals=RetrievalSignals(),
        text_evidence=chunks,
    )


def _requirements(
    *items: RequirementItem,
    document_ids: tuple[str, ...] = (DOCUMENT_A,),
    requires_all: bool = False,
    table_required: bool = True,
) -> AnalysisRequirements:
    return AnalysisRequirements(
        model="test-requirements-model",
        operation=AnalysisOperation.COMPARISON,
        selected_document_ids=document_ids,
        requirements=items,
        requires_all_selected_documents=requires_all,
        table_evidence_required=table_required,
        text_evidence_acceptable=True,
    )


class RequirementsValidationTests(unittest.TestCase):
    def test_llm_output_is_grounded_and_explicit_constraints_are_restored(self) -> None:
        request = _request(
            "Compare revenue for PDF Solutions in 2022 and optionally include "
            "margin if available."
        )
        extraction = RequirementsExtraction(
            operation=AnalysisOperation.SUMMARIZATION,
            requirements=(
                ExtractedRequirement(
                    kind=RequirementKind.METRIC,
                    name="revenue",
                    aliases=("earnings",),
                    required=False,
                    expected_data_type=ExpectedDataType.NUMBER,
                ),
                ExtractedRequirement(
                    kind=RequirementKind.METRIC,
                    name="margin",
                    required=True,
                    expected_data_type=ExpectedDataType.NUMBER,
                ),
                ExtractedRequirement(
                    kind=RequirementKind.ENTITY,
                    name="PDF Solutions",
                ),
                ExtractedRequirement(
                    kind=RequirementKind.ENTITY,
                    name="Invented Company",
                ),
            ),
            table_evidence_required=False,
        )

        artifact = validate_requirements_extraction(
            request=request,
            extraction=extraction,
            model="test-model",
            extraction_attempts=1,
        ).requirements

        self.assertEqual(artifact.operation, AnalysisOperation.COMPARISON)
        self.assertEqual(artifact.selected_document_ids, (DOCUMENT_A,))
        by_id = {item.requirement_id: item for item in artifact.requirements}
        self.assertIn("req_period_2022", by_id)
        self.assertIn("req_entity_pdf_solutions", by_id)
        self.assertNotIn("req_entity_invented_company", by_id)
        self.assertEqual(by_id["req_metric_revenue"].aliases, ())
        self.assertTrue(by_id["req_metric_revenue"].required)
        self.assertFalse(by_id["req_metric_margin"].required)
        self.assertTrue(artifact.table_evidence_required)
        self.assertIn(
            "dropped_ungrounded:entity:invented company",
            artifact.diagnostics.validation_adjustments,
        )

    def test_contradictory_required_filters_are_flagged_for_clarification(
        self,
    ) -> None:
        artifact = validate_requirements_extraction(
            request=_request("Use region Europe but exclude region Europe"),
            extraction=RequirementsExtraction(
                operation=AnalysisOperation.LOOKUP,
                requirements=(
                    ExtractedRequirement(
                        kind=RequirementKind.FILTER,
                        name="region",
                        filter_operator=FilterOperator.EQUALS,
                        filter_values=("Europe",),
                    ),
                    ExtractedRequirement(
                        kind=RequirementKind.FILTER,
                        name="region",
                        filter_operator=FilterOperator.NOT_EQUALS,
                        filter_values=("Europe",),
                    ),
                ),
            ),
            model="test-model",
            extraction_attempts=1,
        ).requirements

        self.assertTrue(artifact.diagnostics.validation_conflicts)


class RequirementsRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_validated_requirements_are_reused_from_cache(self) -> None:
        extraction = RequirementsExtraction(
            operation=AnalysisOperation.TREND,
            requirements=(
                ExtractedRequirement(
                    kind=RequirementKind.METRIC,
                    name="revenue",
                    expected_data_type=ExpectedDataType.NUMBER,
                ),
            ),
            table_evidence_required=True,
        )
        generator = _RequirementsGenerator(extraction)
        cache = _ArtifactCache()
        runner = AnalysisRequirementsRunner(
            cache=cache,
            extractor=RequirementsExtractor(
                generator,
                model="test-model",
            ),
        )
        request = _request("Show the revenue trend")

        first = await runner.run(request)
        second = await runner.run(request)

        self.assertFalse(first.artifact.diagnostics.cache_hit)
        self.assertTrue(second.artifact.diagnostics.cache_hit)
        self.assertEqual(generator.calls, 1)
        self.assertEqual(cache.saves, 1)

    async def test_llm_failure_produces_a_structured_conservative_fallback(self) -> None:
        cache = _ArtifactCache()
        runner = AnalysisRequirementsRunner(
            cache=cache,
            extractor=RequirementsExtractor(
                _RequirementsGenerator(fail=True),
                model="test-model",
            ),
        )

        with patch(
            "scripts.data_analysis_agent.analysis.services.requirements.runner."
            "logger.exception"
        ):
            outcome = await runner.run(_request("Compare values in 2023"))

        self.assertTrue(outcome.artifact.diagnostics.used_fallback)
        self.assertIn(
            IssueCode.REQUIREMENTS_EXTRACTION_FALLBACK,
            [item.code for item in outcome.warnings],
        )
        self.assertIn(
            "req_period_2023",
            [item.requirement_id for item in outcome.artifact.requirements],
        )
        self.assertEqual(cache.saves, 0)


class EvidenceAssessmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_profile_matches_do_not_call_the_ambiguity_llm(self) -> None:
        item = _raw_table(
            table_id="revenue-table",
            document_id=DOCUMENT_A,
            title="Revenue by year",
            document_name="PDF Solutions 2024 Annual Report.pdf",
            columns=[
                {"key": "year", "label": "Year", "type": "string"},
                {
                    "key": "revenue",
                    "label": "Revenue",
                    "type": "number",
                    "unit": "USD million",
                },
            ],
            rows=[{"year": "2023", "revenue": 170}],
        )
        evidence, profiles, metadata = _evidence_and_profiles(item)
        generator = _AmbiguityGenerator()
        runner = EvidenceAssessmentRunner(
            metadata_repository=_MetadataRepository(metadata),
            cache=_ArtifactCache(),
            resolver=AmbiguityResolver(generator, model="test-model"),
        )
        requirements = _requirements(
            RequirementItem(
                requirement_id="req_metric_revenue",
                kind=RequirementKind.METRIC,
                name="revenue",
                expected_data_type=ExpectedDataType.NUMBER,
            )
        )

        outcome = await runner.run(
            request=_request("Compare revenue"),
            requirements=requirements,
            retrieval=_retrieval(),
            evidence=evidence,
            profiles=profiles,
        )

        self.assertEqual(outcome.artifact.decision, ReadinessDecision.READY)
        self.assertEqual(
            outcome.artifact.coverage[0].status,
            CoverageStatus.SUPPORTED,
        )
        self.assertEqual(generator.calls, 0)
        self.assertFalse(outcome.artifact.diagnostics.ambiguity_llm_used)

    async def test_verified_accounting_abbreviation_matches_deterministically(
        self,
    ) -> None:
        item = _raw_table(
            table_id="expense-table",
            document_id=DOCUMENT_A,
            title="Operating expenses",
            document_name="annual-report.pdf",
            columns=[
                {
                    "key": "rd",
                    "label": "R&D Expense",
                    "type": "number",
                    "unit": "USD million",
                }
            ],
            rows=[{"rd": 42}],
        )
        evidence, profiles, metadata = _evidence_and_profiles(item)
        generator = _AmbiguityGenerator()
        runner = EvidenceAssessmentRunner(
            metadata_repository=_MetadataRepository(metadata),
            cache=_ArtifactCache(),
            resolver=AmbiguityResolver(generator, model="test-model"),
        )
        requirements = _requirements(
            RequirementItem(
                requirement_id="req_metric_research_and_development_expense",
                kind=RequirementKind.METRIC,
                name="research and development expense",
                aliases=("R&D expense",),
                expected_data_type=ExpectedDataType.NUMBER,
            )
        )

        outcome = await runner.run(
            request=_request("Show research and development expense"),
            requirements=requirements,
            retrieval=_retrieval(),
            evidence=evidence,
            profiles=profiles,
        )

        self.assertEqual(outcome.artifact.decision, ReadinessDecision.READY)
        self.assertEqual(generator.calls, 0)

    async def test_metric_with_conflicting_unit_is_not_marked_supported(self) -> None:
        item = _raw_table(
            table_id="eur-revenue",
            document_id=DOCUMENT_A,
            title="Revenue",
            document_name="annual-report.pdf",
            columns=[
                {
                    "key": "revenue",
                    "label": "Revenue",
                    "type": "number",
                    "unit": "EUR million",
                }
            ],
            rows=[{"revenue": 100}],
        )
        evidence, profiles, metadata = _evidence_and_profiles(item)
        runner = EvidenceAssessmentRunner(
            metadata_repository=_MetadataRepository(metadata),
            cache=_ArtifactCache(),
            resolver=AmbiguityResolver(
                _AmbiguityGenerator(),
                model="test-model",
            ),
        )
        requirements = _requirements(
            RequirementItem(
                requirement_id="req_metric_revenue",
                kind=RequirementKind.METRIC,
                name="revenue",
                unit="USD million",
                expected_data_type=ExpectedDataType.NUMBER,
            )
        )

        outcome = await runner.run(
            request=_request("Show revenue in USD million"),
            requirements=requirements,
            retrieval=_retrieval(),
            evidence=evidence,
            profiles=profiles,
        )

        self.assertEqual(
            outcome.artifact.coverage[0].status,
            CoverageStatus.CONFLICTING,
        )
        self.assertEqual(
            outcome.artifact.decision,
            ReadinessDecision.NEEDS_CLARIFICATION,
        )

    async def test_cross_document_missing_table_values_route_to_text_extraction(
        self,
    ) -> None:
        item = _raw_table(
            table_id="company-a-revenue",
            document_id=DOCUMENT_A,
            title="Revenue by year",
            document_name="Company A Annual Report.pdf",
            columns=[
                {"key": "year", "label": "Year", "type": "string"},
                {"key": "revenue", "label": "Revenue", "type": "number"},
            ],
            rows=[{"year": "2023", "revenue": 100}],
        )
        evidence, profiles, metadata = _evidence_and_profiles(item)
        runner = EvidenceAssessmentRunner(
            metadata_repository=_MetadataRepository(metadata),
            cache=_ArtifactCache(),
            resolver=AmbiguityResolver(
                _AmbiguityGenerator(),
                model="test-model",
            ),
        )
        requirements = _requirements(
            RequirementItem(
                requirement_id="req_metric_revenue",
                kind=RequirementKind.METRIC,
                name="revenue",
                expected_data_type=ExpectedDataType.NUMBER,
            ),
            RequirementItem(
                requirement_id="req_period_2023",
                kind=RequirementKind.PERIOD,
                name="2023",
                expected_data_type=ExpectedDataType.DATE,
            ),
            document_ids=(DOCUMENT_A, DOCUMENT_B),
            requires_all=True,
        )
        chunk = TextEvidenceReference(
            chunk_id="chunk-b-1",
            document_id=DOCUMENT_B,
            document_name="Company B Annual Report.pdf",
            page_number=12,
            text="Total revenue for 2023 was 200 million.",
        )

        outcome = await runner.run(
            request=_request(
                "Compare revenue in 2023 across both reports",
                document_ids=(DOCUMENT_A, DOCUMENT_B),
            ),
            requirements=requirements,
            retrieval=_retrieval(chunk),
            evidence=evidence,
            profiles=profiles,
        )

        self.assertEqual(
            outcome.artifact.decision,
            ReadinessDecision.NEEDS_TEXT_EXTRACTION,
        )
        self.assertTrue(
            all(
                item.status == CoverageStatus.PARTIAL
                for item in outcome.artifact.coverage
            )
        )
        self.assertTrue(
            any(
                reference.evidence_kind == EvidenceKind.TEXT_CHUNK
                for item in outcome.artifact.coverage
                for reference in item.evidence
            )
        )
        document_b = next(
            item
            for item in outcome.artifact.document_coverage
            if item.document_id == DOCUMENT_B
        )
        self.assertEqual(document_b.status, CoverageStatus.PARTIAL)

    async def test_unresolved_metric_entity_scope_cannot_be_marked_ready(self) -> None:
        item = _raw_table(
            table_id="company-a-revenue",
            document_id=DOCUMENT_A,
            title="Revenue",
            document_name="Company A Annual Report.pdf",
            columns=[
                {"key": "revenue", "label": "Revenue", "type": "number"},
            ],
            rows=[{"revenue": 100}],
        )
        evidence, profiles, metadata = _evidence_and_profiles(item)
        runner = EvidenceAssessmentRunner(
            metadata_repository=_MetadataRepository(metadata),
            cache=_ArtifactCache(),
            resolver=AmbiguityResolver(
                _AmbiguityGenerator(),
                model="test-model",
            ),
        )
        requirements = _requirements(
            RequirementItem(
                requirement_id="req_metric_revenue",
                kind=RequirementKind.METRIC,
                name="revenue",
                entity_names=("Company A", "Company B"),
                expected_data_type=ExpectedDataType.NUMBER,
            ),
            document_ids=(DOCUMENT_A, DOCUMENT_B),
        )

        outcome = await runner.run(
            request=_request(
                "Compare Company A and Company B revenue",
                document_ids=(DOCUMENT_A, DOCUMENT_B),
            ),
            requirements=requirements,
            retrieval=_retrieval(),
            evidence=evidence,
            profiles=profiles,
        )

        self.assertEqual(
            outcome.artifact.coverage[0].status,
            CoverageStatus.PARTIAL,
        )
        self.assertEqual(
            outcome.artifact.decision,
            ReadinessDecision.NEEDS_RETRIEVAL_REPAIR,
        )

    async def test_only_ambiguous_labels_use_one_bounded_llm_batch(self) -> None:
        item = _raw_table(
            table_id="margin-table",
            document_id=DOCUMENT_A,
            title="Profitability",
            document_name="annual-report.pdf",
            columns=[
                {
                    "key": "operating_margin",
                    "label": "Operating margin",
                    "type": "number",
                    "unit": "percent",
                }
            ],
            rows=[{"operating_margin": 12.5}],
        )
        evidence, profiles, metadata = _evidence_and_profiles(item)
        generator = _AmbiguityGenerator(decision="match")
        cache = _ArtifactCache()
        runner = EvidenceAssessmentRunner(
            metadata_repository=_MetadataRepository(metadata),
            cache=cache,
            resolver=AmbiguityResolver(generator, model="test-model"),
        )
        requirements = _requirements(
            RequirementItem(
                requirement_id="req_metric_operating_profit_margin",
                kind=RequirementKind.METRIC,
                name="operating profit margin",
                expected_data_type=ExpectedDataType.NUMBER,
            )
        )

        first = await runner.run(
            request=_request("Show operating profit margin"),
            requirements=requirements,
            retrieval=_retrieval(),
            evidence=evidence,
            profiles=profiles,
        )
        second = await runner.run(
            request=_request("Show operating profit margin"),
            requirements=requirements,
            retrieval=_retrieval(),
            evidence=evidence,
            profiles=profiles,
        )

        self.assertEqual(first.artifact.decision, ReadinessDecision.READY)
        self.assertTrue(first.artifact.diagnostics.ambiguity_llm_used)
        self.assertEqual(first.artifact.diagnostics.ambiguity_resolved_count, 1)
        self.assertTrue(second.artifact.diagnostics.cache_hit)
        self.assertEqual(generator.calls, 1)


class MongoPhaseFourAndFiveRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_requirements_cache_is_tenant_scoped_and_upserted(self) -> None:
        requirements = _requirements(
            RequirementItem(
                requirement_id="req_metric_revenue",
                kind=RequirementKind.METRIC,
                name="revenue",
            )
        )
        collection = _MongoCollection(
            [{"requirements": requirements.model_dump(mode="json")}]
        )
        database = type(
            "Database",
            (),
            {"analysis_requirements_cache": collection},
        )()
        cache = MongoRequirementsCache()

        with patch(
            "scripts.data_analysis_agent.analysis.repositories."
            "requirements_cache.get_db",
            return_value=database,
        ):
            loaded = await cache.load(user_id="user-1", cache_key="key-1")
            await cache.save(
                user_id="user-1",
                cache_key="key-1",
                requirements=requirements,
            )

        self.assertEqual(loaded, requirements)
        self.assertEqual(
            collection.find_one_calls[0][0],
            {"user_id": "user-1", "cache_key": "key-1"},
        )
        self.assertTrue(collection.update_calls[0][2])

    async def test_assessment_cache_is_tenant_scoped_and_upserted(self) -> None:
        assessment = EvidenceAssessment(
            ambiguity_model="test-model",
            decision=ReadinessDecision.NEEDS_RETRIEVAL_REPAIR,
            coverage=(
                RequirementCoverage(
                    requirement_id="req_metric_revenue",
                    status=CoverageStatus.MISSING,
                    confidence=0,
                    reason="No evidence.",
                ),
            ),
            document_coverage=(
                DocumentCoverage(
                    document_id=DOCUMENT_A,
                    required=True,
                    status=CoverageStatus.MISSING,
                ),
            ),
            required_count=1,
            supported_count=0,
            partial_count=0,
            missing_count=1,
            conflicting_count=0,
            ambiguous_count=0,
        )
        collection = _MongoCollection(
            [{"assessment": assessment.model_dump(mode="json")}]
        )
        database = type(
            "Database",
            (),
            {"evidence_assessments_cache": collection},
        )()
        cache = MongoAssessmentCache()

        with patch(
            "scripts.data_analysis_agent.analysis.repositories."
            "assessment_cache.get_db",
            return_value=database,
        ):
            loaded = await cache.load(user_id="user-1", cache_key="key-2")
            await cache.save(
                user_id="user-1",
                cache_key="key-2",
                assessment=assessment,
            )

        self.assertEqual(loaded, assessment)
        self.assertEqual(collection.find_one_calls[0][0]["user_id"], "user-1")
        self.assertTrue(collection.update_calls[0][2])

    async def test_assessment_metadata_projection_never_loads_rows(self) -> None:
        collection = _MongoCollection(
            [
                {
                    "table_id": "table-1",
                    "document_id": DOCUMENT_A,
                    "title": "Revenue",
                    "summary": "Revenue by year.",
                    "keywords": ["revenue", "year"],
                }
            ]
        )
        database = type("Database", (), {"structured_tables": collection})()

        with patch(
            "scripts.data_analysis_agent.analysis.repositories."
            "assessment_metadata.get_db",
            return_value=database,
        ):
            metadata = (
                await MongoAssessmentMetadataRepository().load_table_metadata(
                    user_id="user-1",
                    document_ids=[DOCUMENT_A],
                    table_ids=["table-1"],
                )
            )

        query, projection = collection.find_calls[0]
        self.assertEqual(query["user_id"], "user-1")
        self.assertNotIn("rows", projection)
        self.assertEqual(metadata["table-1"].summary, "Revenue by year.")


if __name__ == "__main__":
    unittest.main()
