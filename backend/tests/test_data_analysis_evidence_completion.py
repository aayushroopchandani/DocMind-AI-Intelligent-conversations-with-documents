from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

from scripts.data_analysis_agent.analysis.models import (
    AnalysisOperation,
    AnalysisRequest,
    AnalysisRequirements,
    AssessmentDiagnostics,
    CompletionAttempt,
    CompletionAttemptOutcome,
    CompletionStage,
    CompletionStatus,
    CoverageStatus,
    DATASET_PROFILER_VERSION,
    DatasetProfiles,
    DocumentCoverage,
    EvidenceAssessment,
    EvidenceFact,
    EvidencePackage,
    ExpectedDataType,
    FactDimension,
    ProposedEvidenceFact,
    ReadinessDecision,
    RequirementCoverage,
    RequirementItem,
    RequirementKind,
    RequirementOrigin,
    RetrievalResult,
    RetrievalSignals,
    TableCandidateReference,
    TextEvidenceReference,
    TextExtractionCacheEntry,
    TextExtractionResponse,
)
from scripts.data_analysis_agent.analysis.repositories import (
    DerivedDatasetWrite,
    HydrationSourceBatch,
    MongoDerivedDatasetRepository,
    MongoTextExtractionCache,
)
from scripts.data_analysis_agent.analysis.services import (
    DatasetProfilingRunner,
    EvidenceAssessmentRunner,
)
from scripts.data_analysis_agent.analysis.services.completion import (
    CandidateRescueSelector,
    EvidenceCompletionRunner,
    QdrantTargetedRepairRetriever,
    StructuredTextEvidenceExtractor,
    TargetedRepairResult,
    TextCompletionOutcome,
    TextEvidenceCompletionService,
    TextExtractionTask,
    build_derived_dataset_writes,
    build_repair_queries,
    build_text_extraction_tasks,
    validate_text_extraction,
)


DOCUMENT_ID = "a" * 64


def _requirements() -> AnalysisRequirements:
    return AnalysisRequirements(
        model="test-model",
        operation=AnalysisOperation.LOOKUP,
        selected_document_ids=(DOCUMENT_ID,),
        requirements=(
            RequirementItem(
                requirement_id="req_metric_total_revenue",
                kind=RequirementKind.METRIC,
                name="total revenue",
                aliases=("revenue",),
                expected_data_type=ExpectedDataType.NUMBER,
                unit="USD million",
                origin=RequirementOrigin.LLM,
            ),
            RequirementItem(
                requirement_id="req_period_2023",
                kind=RequirementKind.PERIOD,
                name="2023",
                expected_data_type=ExpectedDataType.DATE,
                origin=RequirementOrigin.EXPLICIT_GUARD,
            ),
        ),
        table_evidence_required=True,
        text_evidence_acceptable=True,
    )


def _assessment(
    *,
    decision: ReadinessDecision = ReadinessDecision.NEEDS_TEXT_EXTRACTION,
) -> EvidenceAssessment:
    return EvidenceAssessment(
        ambiguity_model="test-ambiguity",
        decision=decision,
        coverage=(
            RequirementCoverage(
                requirement_id="req_metric_total_revenue",
                status=CoverageStatus.PARTIAL,
                confidence=0.72,
                reason="Relevant text requires extraction.",
                text_evidence_available=True,
            ),
            RequirementCoverage(
                requirement_id="req_period_2023",
                status=CoverageStatus.PARTIAL,
                confidence=0.72,
                reason="Relevant text requires extraction.",
                text_evidence_available=True,
            ),
        ),
        document_coverage=(
            DocumentCoverage(
                document_id=DOCUMENT_ID,
                document_name="report.pdf",
                required=True,
                status=CoverageStatus.PARTIAL,
                text_chunk_ids=("chunk-1",),
            ),
        ),
        required_count=2,
        supported_count=0,
        partial_count=2,
        missing_count=0,
        conflicting_count=0,
        ambiguous_count=0,
        diagnostics=AssessmentDiagnostics(),
    )


def _chunk(text: str | None = None) -> TextEvidenceReference:
    return TextEvidenceReference(
        chunk_id="chunk-1",
        document_id=DOCUMENT_ID,
        document_name="report.pdf",
        page_number=44,
        text=(
            text
            or "PDF Solutions reported total revenue of $165.8 million in 2023."
        ),
        relevance_score=0.9,
    )


def _fact(
    *,
    period: str = "2023",
    value: str = "165.8",
    suffix: str = "1",
) -> EvidenceFact:
    text = f"Total revenue was ${value} million in {period}."
    return EvidenceFact(
        fact_id=f"fact_{suffix * 24}",
        requirement_id="req_metric_total_revenue",
        entity="PDF Solutions",
        metric="total revenue",
        raw_value=value,
        normalized_value=value,
        unit="USD million",
        period=period,
        dimensions=(FactDimension(name="company", value="PDF Solutions"),),
        document_id=DOCUMENT_ID,
        chunk_id=f"chunk-{suffix}",
        page=44,
        source_span=text,
        span_start=0,
        span_end=len(text),
        chunk_hash=hashlib.sha256(text.encode()).hexdigest(),
        confidence=0.95,
        model="test-extractor",
    )


class _ArtifactCache:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def load(self, *, user_id: str, cache_key: str) -> Any:
        return self.values.get((user_id, cache_key))

    async def save(
        self,
        *,
        user_id: str,
        cache_key: str,
        **kwargs: Any,
    ) -> None:
        self.values[(user_id, cache_key)] = kwargs["assessment"]


class _MetadataRepository:
    async def load_table_metadata(self, **_kwargs: Any) -> dict[str, Any]:
        return {}


class _NeverAmbiguityResolver:
    model = "test-ambiguity"

    async def resolve(self, _values: Any) -> dict[str, Any]:
        raise AssertionError("validated facts must not need ambiguity resolution")


class _TextCache:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], TextExtractionCacheEntry] = {}

    async def load(
        self,
        *,
        user_id: str,
        cache_key: str,
    ) -> TextExtractionCacheEntry | None:
        return self.values.get((user_id, cache_key))

    async def save(
        self,
        *,
        user_id: str,
        cache_key: str,
        entry: TextExtractionCacheEntry,
    ) -> None:
        self.values[(user_id, cache_key)] = entry


class _ExtractionGenerator:
    def __init__(self, response: TextExtractionResponse) -> None:
        self.response = response
        self.calls = 0

    async def ainvoke(self, _input: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        return self.response


class _RetryExtractionGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, _input: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            raise ValueError("truncated JSON")
        return TextExtractionResponse(status="absent")


class _DerivedRepository:
    def __init__(self) -> None:
        self.values: list[DerivedDatasetWrite] = []

    async def save(
        self,
        *,
        user_id: str,
        value: DerivedDatasetWrite,
    ) -> Any:
        self.values.append(value)
        return value.reference


class _CapturingTextRetriever:
    def __init__(self) -> None:
        self.states: list[dict[str, Any]] = []

    async def retrieve(self, state: Any) -> list[dict[str, Any]]:
        self.states.append(dict(state))
        return [
            {
                "chunk_id": "repair-chunk",
                "text": "Total revenue was $165.8 million in 2023.",
                "metadata": {
                    "doc_id": DOCUMENT_ID,
                    "source": "report.pdf",
                    "page_number": 44,
                },
                "rrf_score": 0.1,
                "matched_queries": [state["query"]],
                "retrieval_modes": ["dense", "sparse"],
            }
        ]


class _CapturingTableRetriever:
    def __init__(self) -> None:
        self.states: list[dict[str, Any]] = []

    async def retrieve(self, state: Any) -> list[dict[str, Any]]:
        self.states.append(dict(state))
        return [
            {
                "table_id": "repair-table",
                "document_id": DOCUMENT_ID,
                "title": "Revenue",
                "summary": "Total revenue in 2023",
                "columns": ["metric", "2023"],
                "metrics": ["total revenue"],
                "units": ["USD million"],
                "keywords": [],
                "rrf_score": 0.1,
                "matched_queries": [state["query"]],
                "retrieval_modes": ["dense", "sparse"],
            }
        ]


class _RepairCache:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], Any] = {}

    async def load(self, *, user_id: str, cache_key: str) -> Any:
        return self.values.get((user_id, cache_key))

    async def save(
        self,
        *,
        user_id: str,
        cache_key: str,
        entry: Any,
    ) -> None:
        self.values[(user_id, cache_key)] = entry


class _MongoCollection:
    def __init__(self, value: dict[str, Any] | None = None) -> None:
        self.value = value
        self.find_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.update_calls: list[tuple[dict[str, Any], dict[str, Any], bool]] = []

    async def find_one(
        self,
        query: dict[str, Any],
        projection: dict[str, Any],
    ) -> dict[str, Any] | None:
        self.find_calls.append((query, projection))
        return self.value

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool,
    ) -> None:
        self.update_calls.append((query, update, upsert))


class _MongoDatabase:
    def __init__(self, collection: _MongoCollection) -> None:
        self.collection = collection

    def __getitem__(self, _name: str) -> _MongoCollection:
        return self.collection


class EvidenceCompletionUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_text_extraction_is_retried_once(self) -> None:
        generator = _RetryExtractionGenerator()
        extractor = StructuredTextEvidenceExtractor(
            generator,
            model="test-extractor",
        )

        response, attempts = await extractor.extract(
            TextExtractionTask(
                document_id=DOCUMENT_ID,
                target_requirement_ids=("req_metric_total_revenue",),
                requirements=_requirements().requirements,
                chunks=(_chunk(),),
            )
        )

        self.assertEqual(response.status, "absent")
        self.assertEqual(attempts, 2)
        self.assertEqual(generator.calls, 2)

    def test_parent_retrieval_keeps_compact_prefusion_candidates(self) -> None:
        result = RetrievalResult.from_retrieval_state(
            {
                "retrieval_scope": "normal",
                "table_intent": "required",
                "retrieved_tables": [
                    {
                        "table_id": "unused-table",
                        "document_id": DOCUMENT_ID,
                        "title": "Revenue",
                        "summary": "Revenue by year",
                        "columns": ["year", "revenue"],
                        "metrics": ["revenue"],
                        "units": ["USD million"],
                        "keywords": ["financial"],
                        "rows": [{"must_not": "enter parent state"}],
                    }
                ],
                "final_tables": [],
                "final_text_chunks": [],
            }
        )

        self.assertEqual(len(result.table_candidates), 1)
        self.assertEqual(result.table_candidates[0].table_id, "unused-table")
        self.assertNotIn("rows", result.table_candidates[0].model_dump())

    def test_text_prompt_uses_a_bounded_source_hashed_window(self) -> None:
        text = (
            ("narrative " * 3000)
            + "PDF Solutions total revenue was $165.8 million in 2023."
        )
        chunk = _chunk(text)
        tasks = build_text_extraction_tasks(
            requirements=_requirements(),
            assessment=_assessment(),
            chunks=(chunk,),
            max_chars_per_document=1000,
        )

        self.assertEqual(len(tasks), 1)
        bounded = tasks[0].chunks[0]
        self.assertEqual(len(bounded.text), 1000)
        self.assertIn("$165.8 million", bounded.text)
        self.assertEqual(
            bounded.content_hash,
            hashlib.sha256(text.encode()).hexdigest(),
        )
        self.assertGreater(bounded.text_offset, 0)
        self.assertEqual(chunk.text, text)

    def test_candidate_rescue_is_scoped_to_missing_requirements(self) -> None:
        candidates = (
            TableCandidateReference(
                table_id="revenue-table",
                document_id=DOCUMENT_ID,
                title="Total revenue",
                summary="Total revenue for 2022 and 2023",
                expected_columns=("Metric", "2022", "2023"),
                expected_metrics=("total revenue",),
            ),
            TableCandidateReference(
                table_id="employee-table",
                document_id=DOCUMENT_ID,
                title="Employees",
                summary="Employee headcount",
            ),
        )

        selected = CandidateRescueSelector().select(
            requirements=_requirements(),
            assessment=_assessment(),
            candidates=candidates,
            used_table_ids=set(),
        )

        self.assertEqual([item.candidate.table_id for item in selected], ["revenue-table"])
        self.assertIn(
            "req_metric_total_revenue",
            selected[0].requirement_ids,
        )
        self.assertIn("req_period_2023", selected[0].requirement_ids)

    def test_candidate_rescue_requires_explicit_category_coverage(self) -> None:
        requirements = AnalysisRequirements(
            model="test-model",
            operation=AnalysisOperation.COMPARISON,
            selected_document_ids=(DOCUMENT_ID,),
            requirements=(
                RequirementItem(
                    requirement_id="req_metric_revenue",
                    kind=RequirementKind.METRIC,
                    name="revenue",
                    expected_data_type=ExpectedDataType.NUMBER,
                ),
                RequirementItem(
                    requirement_id="req_filter_customer",
                    kind=RequirementKind.FILTER,
                    name="customer",
                    filter_operator="in",
                    filter_values=("A", "B", "C"),
                    expected_data_type=ExpectedDataType.STRING,
                ),
            ),
            table_evidence_required=True,
        )
        assessment = EvidenceAssessment(
            ambiguity_model="test",
            decision=ReadinessDecision.NEEDS_CANDIDATE_RESCUE,
            coverage=(
                RequirementCoverage(
                    requirement_id="req_metric_revenue",
                    status=CoverageStatus.MISSING,
                    confidence=0,
                    reason="missing",
                ),
                RequirementCoverage(
                    requirement_id="req_filter_customer",
                    status=CoverageStatus.MISSING,
                    confidence=0,
                    reason="missing",
                ),
            ),
            document_coverage=(
                DocumentCoverage(
                    document_id=DOCUMENT_ID,
                    required=True,
                    status=CoverageStatus.MISSING,
                ),
            ),
            required_count=2,
            supported_count=0,
            partial_count=0,
            missing_count=2,
            conflicting_count=0,
            ambiguous_count=0,
        )
        candidates = (
            TableCandidateReference(
                table_id="customer-table",
                document_id=DOCUMENT_ID,
                title="Customer revenue concentration",
                summary="Revenue percentages for Customers A, B, and C.",
                expected_columns=("Customer", "2024", "2023", "2022"),
                expected_metrics=("revenue",),
            ),
            TableCandidateReference(
                table_id="analytics-table",
                document_id=DOCUMENT_ID,
                title="Analytics revenue",
                summary="Total revenue and gross profit by year.",
                expected_columns=("Revenue", "2024", "2023", "2022"),
                expected_metrics=("revenue",),
            ),
        )

        selected = CandidateRescueSelector().select(
            requirements=requirements,
            assessment=assessment,
            candidates=candidates,
            used_table_ids=set(),
        )

        self.assertEqual(
            [item.candidate.table_id for item in selected],
            ["customer-table"],
        )
        self.assertIn("req_filter_customer", selected[0].requirement_ids)

    def test_text_facts_require_verbatim_numeric_provenance(self) -> None:
        chunk = _chunk()
        accepted = validate_text_extraction(
            response=TextExtractionResponse(
                status="evidence",
                facts=(
                    ProposedEvidenceFact(
                        requirement_id="req_metric_total_revenue",
                        entity="PDF Solutions",
                        metric="total revenue",
                        raw_value="$165.8",
                        unit="USD million",
                        period="2023",
                        document_id=DOCUMENT_ID,
                        chunk_id="chunk-1",
                        source_span=chunk.text,
                        confidence=0.96,
                    ),
                ),
            ),
            requirements=_requirements().requirements,
            chunks=(chunk,),
            model="test-extractor",
        )
        rejected = validate_text_extraction(
            response=TextExtractionResponse(
                status="evidence",
                facts=(
                    ProposedEvidenceFact(
                        requirement_id="req_metric_total_revenue",
                        entity="PDF Solutions",
                        metric="total revenue",
                        raw_value="$999.0",
                        unit="USD million",
                        period="2023",
                        document_id=DOCUMENT_ID,
                        chunk_id="chunk-1",
                        source_span=chunk.text,
                        confidence=0.99,
                    ),
                ),
            ),
            requirements=_requirements().requirements,
            chunks=(chunk,),
            model="test-extractor",
        )
        period_as_value = validate_text_extraction(
            response=TextExtractionResponse(
                status="evidence",
                facts=(
                    ProposedEvidenceFact(
                        requirement_id="req_metric_total_revenue",
                        entity="PDF Solutions",
                        metric="total revenue",
                        raw_value="2023",
                        unit="USD million",
                        period=None,
                        document_id=DOCUMENT_ID,
                        chunk_id="chunk-1",
                        source_span=chunk.text,
                        confidence=0.99,
                    ),
                ),
            ),
            requirements=_requirements().requirements,
            chunks=(chunk,),
            model="test-extractor",
        )

        self.assertEqual(len(accepted.facts), 1)
        self.assertEqual(accepted.facts[0].normalized_value, "165.8")
        self.assertEqual(rejected.facts, ())
        self.assertEqual(len(rejected.rejected), 1)
        self.assertEqual(period_as_value.facts, ())

    def test_text_fact_normalizes_decimal_comma_scale_and_document_period(
        self,
    ) -> None:
        chunk = TextEvidenceReference(
            chunk_id="impact-chunk",
            document_id=DOCUMENT_ID,
            document_name="amazon-conservation-team_2023.pdf",
            page_number=9,
            text=(
                "IMPACT SUMMARY\n"
                "9,9 MILLION ACRES UNDER IMPROVED SUSTAINABLE MANAGEMENT"
            ),
        )
        requirements = (
            RequirementItem(
                requirement_id="req_metric_managed_acres",
                kind=RequirementKind.METRIC,
                name="acres under improved sustainable management",
                expected_data_type=ExpectedDataType.NUMBER,
            ),
            RequirementItem(
                requirement_id="req_period_2023",
                kind=RequirementKind.PERIOD,
                name="2023",
                expected_data_type=ExpectedDataType.DATE,
            ),
        )

        result = validate_text_extraction(
            response=TextExtractionResponse(
                status="evidence",
                facts=(
                    ProposedEvidenceFact(
                        requirement_id="req_metric_managed_acres",
                        entity="Amazon Conservation Team",
                        metric="acres under improved sustainable management",
                        raw_value="9,9 MILLION",
                        unit="acres",
                        period=None,
                        document_id=DOCUMENT_ID,
                        chunk_id="impact-chunk",
                        source_span=(
                            "9,9 MILLION ACRES UNDER IMPROVED "
                            "SUSTAINABLE MANAGEMENT"
                        ),
                        confidence=0.99,
                    ),
                ),
            ),
            requirements=requirements,
            chunks=(chunk,),
            model="test-extractor",
        )

        self.assertEqual(len(result.facts), 1)
        self.assertEqual(result.facts[0].normalized_value, "9.9")
        self.assertEqual(result.facts[0].unit, "million acres")
        self.assertEqual(result.facts[0].period, "2023")

    def test_area_measure_cannot_satisfy_a_count_metric(self) -> None:
        chunk = TextEvidenceReference(
            chunk_id="reserve-chunk",
            document_id=DOCUMENT_ID,
            document_name="amazon-conservation-team_2023.pdf",
            page_number=9,
            text=(
                "2,7 MILLION ACRES OF INDIGENOUS RESERVES ESTABLISHED\n"
                "33 NEW INDIGENOUS RESERVES"
            ),
        )
        requirements = (
            RequirementItem(
                requirement_id="req_metric_new_indigenous_reserves",
                kind=RequirementKind.METRIC,
                name="new Indigenous reserves",
                expected_data_type=ExpectedDataType.NUMBER,
            ),
            RequirementItem(
                requirement_id="req_period_2023",
                kind=RequirementKind.PERIOD,
                name="2023",
                expected_data_type=ExpectedDataType.DATE,
            ),
        )

        result = validate_text_extraction(
            response=TextExtractionResponse(
                status="evidence",
                facts=(
                    ProposedEvidenceFact(
                        requirement_id="req_metric_new_indigenous_reserves",
                        entity="Amazon Conservation Team",
                        metric="new Indigenous reserves",
                        raw_value="2,7 MILLION",
                        unit="acres",
                        period=None,
                        document_id=DOCUMENT_ID,
                        chunk_id="reserve-chunk",
                        source_span=(
                            "2,7 MILLION ACRES OF INDIGENOUS "
                            "RESERVES ESTABLISHED"
                        ),
                        confidence=0.99,
                    ),
                    ProposedEvidenceFact(
                        requirement_id="req_metric_new_indigenous_reserves",
                        entity="Amazon Conservation Team",
                        metric="new Indigenous reserves",
                        raw_value="33",
                        unit=None,
                        period=None,
                        document_id=DOCUMENT_ID,
                        chunk_id="reserve-chunk",
                        source_span="33 NEW INDIGENOUS RESERVES",
                        confidence=0.99,
                    ),
                ),
            ),
            requirements=requirements,
            chunks=(chunk,),
            model="test-extractor",
        )

        self.assertEqual(len(result.facts), 1)
        self.assertEqual(result.facts[0].normalized_value, "33")
        self.assertEqual(len(result.rejected), 1)
        self.assertIn("cannot quantify", result.rejected[0].reason)

    def test_text_fact_recovers_grounded_span_with_whitespace_differences(
        self,
    ) -> None:
        text = (
            "As of December 31, 2024, the aggregate amount allocated to the "
            "remaining performance obligations was $221.4 million."
        )
        chunk = TextEvidenceReference(
            chunk_id="obligations-chunk",
            document_id=DOCUMENT_ID,
            document_name="PDF Solutions 2024 Annual Report.pdf",
            page_number=68,
            text=text,
        )
        requirements = (
            RequirementItem(
                requirement_id="req_metric_remaining_obligations",
                kind=RequirementKind.METRIC,
                name="remaining performance obligations",
                expected_data_type=ExpectedDataType.NUMBER,
                unit="USD million",
            ),
        )

        result = validate_text_extraction(
            response=TextExtractionResponse(
                status="evidence",
                facts=(
                    ProposedEvidenceFact(
                        requirement_id="req_metric_remaining_obligations",
                        entity="PDF Solutions",
                        metric="remaining performance obligations",
                        raw_value="$221.4",
                        unit="USD million",
                        period="2024",
                        document_id=DOCUMENT_ID,
                        chunk_id="obligations-chunk",
                        source_span=(
                            "remaining performance obligations   was "
                            "$221.4 million"
                        ),
                        confidence=0.96,
                    ),
                ),
            ),
            requirements=requirements,
            chunks=(chunk,),
            model="test-extractor",
        )

        self.assertEqual(len(result.facts), 1)
        self.assertEqual(result.facts[0].raw_value, "$221.4")
        self.assertIn("$221.4 million", result.facts[0].source_span)

    async def test_text_extraction_is_cached_and_small_results_stay_as_facts(
        self,
    ) -> None:
        chunk = _chunk()
        generator = _ExtractionGenerator(
            TextExtractionResponse(
                status="evidence",
                facts=(
                    ProposedEvidenceFact(
                        requirement_id="req_metric_total_revenue",
                        entity="PDF Solutions",
                        metric="total revenue",
                        raw_value="$165.8",
                        unit="USD million",
                        period="2023",
                        document_id=DOCUMENT_ID,
                        chunk_id="chunk-1",
                        source_span=chunk.text,
                        confidence=0.96,
                    ),
                ),
            )
        )
        derived_repository = _DerivedRepository()
        service = TextEvidenceCompletionService(
            cache=_TextCache(),
            derived_repository=derived_repository,
            extractor=StructuredTextEvidenceExtractor(
                generator,
                model="test-extractor",
            ),
        )

        first = await service.run(
            user_id="user-1",
            requirements=_requirements(),
            assessment=_assessment(),
            chunks=(chunk,),
            stage=CompletionStage.EXISTING_TEXT_EXTRACTION,
        )
        second = await service.run(
            user_id="user-1",
            requirements=_requirements(),
            assessment=_assessment(),
            chunks=(chunk,),
            stage=CompletionStage.EXISTING_TEXT_EXTRACTION,
        )

        self.assertEqual(generator.calls, 1)
        self.assertEqual(len(first.facts), 1)
        self.assertEqual(first.derived_datasets, ())
        self.assertEqual(derived_repository.values, [])
        self.assertTrue(second.attempts[0].cache_hit)

    def test_coherent_three_period_series_becomes_a_derived_dataset(self) -> None:
        writes = build_derived_dataset_writes(
            (
                _fact(period="2021", value="130.0", suffix="1"),
                _fact(period="2022", value="148.5", suffix="2"),
                _fact(period="2023", value="165.8", suffix="3"),
            )
        )

        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].reference.row_count, 3)
        self.assertEqual(writes[0].reference.reusability_status, "cached")
        self.assertEqual(len(writes[0].rows), 3)

    def test_repair_queries_include_only_incomplete_requirement_context(self) -> None:
        attempted: set[str] = set()
        queries = build_repair_queries(
            requirements=_requirements(),
            assessment=_assessment(),
            attempted_queries=attempted,
            attempt=1,
        )

        self.assertEqual(
            queries,
            ("total revenue 2023 USD million table",),
        )
        self.assertNotIn("compare", queries[0])

    async def test_mongo_text_cache_is_tenant_scoped_and_ttl_aware(self) -> None:
        entry = TextExtractionCacheEntry(
            status="accepted",
            facts=(_fact(),),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        collection = _MongoCollection(
            {"entry": entry.model_dump(mode="python")}
        )
        repository = MongoTextExtractionCache()
        with patch(
            "scripts.data_analysis_agent.analysis.repositories.completion_cache.get_db",
            return_value=_MongoDatabase(collection),
        ):
            loaded = await repository.load(
                user_id="user-1",
                cache_key="cache-1",
            )
            await repository.save(
                user_id="user-1",
                cache_key="cache-1",
                entry=entry,
            )

        self.assertEqual(loaded, entry)
        self.assertEqual(
            collection.find_calls[0][0]["user_id"],
            "user-1",
        )
        self.assertIn("$gt", collection.find_calls[0][0]["expires_at"])
        self.assertEqual(
            collection.update_calls[0][0],
            {"user_id": "user-1", "cache_key": "cache-1"},
        )
        self.assertTrue(collection.update_calls[0][2])

    async def test_derived_rows_are_persisted_only_in_derived_collection(
        self,
    ) -> None:
        write = build_derived_dataset_writes(
            (
                _fact(period="2021", value="130.0", suffix="1"),
                _fact(period="2022", value="148.5", suffix="2"),
                _fact(period="2023", value="165.8", suffix="3"),
            )
        )[0]
        collection = _MongoCollection()
        repository = MongoDerivedDatasetRepository()
        with patch(
            "scripts.data_analysis_agent.analysis.repositories.derived_datasets.get_db",
            return_value=_MongoDatabase(collection),
        ):
            reference = await repository.save(
                user_id="user-1",
                value=write,
            )

        query, update, upsert = collection.update_calls[0]
        self.assertEqual(query["user_id"], "user-1")
        self.assertEqual(
            query["derived_dataset_id"],
            reference.derived_dataset_id,
        )
        self.assertEqual(update["$set"]["origin"], "llm_text_extraction")
        self.assertEqual(len(update["$set"]["rows"]), 3)
        self.assertNotIn("rows", reference.model_dump())
        self.assertTrue(upsert)

    async def test_targeted_repair_reuses_existing_hybrid_retrieval_contract(
        self,
    ) -> None:
        text = _CapturingTextRetriever()
        tables = _CapturingTableRetriever()
        cache = _RepairCache()
        retriever = QdrantTargetedRepairRetriever(
            text_retriever=text,
            table_retriever=tables,
            cache=cache,
        )

        kwargs = {
            "request": AnalysisRequest(
                user_id="user-1",
                chat_id="chat-1",
                query="original broad question",
                document_ids=(DOCUMENT_ID,),
            ),
            "requirements": _requirements(),
            "assessment": _assessment(),
            "attempt": 1,
        }
        result = await retriever.retrieve(
            **kwargs,
            attempted_queries=set(),
        )
        cached = await retriever.retrieve(
            **kwargs,
            attempted_queries=set(),
        )

        self.assertEqual(text.states[0]["retrieval_scope"], "broad")
        self.assertEqual(text.states[0], tables.states[0])
        self.assertNotEqual(text.states[0]["query"], "original broad question")
        self.assertEqual(result.document_ids, (DOCUMENT_ID,))
        self.assertEqual(result.text_evidence[0].chunk_id, "repair-chunk")
        self.assertEqual(
            result.table_candidates[0].table_id,
            "repair-table",
        )
        self.assertEqual(len(text.states), 1)
        self.assertEqual(len(tables.states), 1)
        self.assertTrue(cached.cache_hit)
        self.assertEqual(cached.table_candidates, result.table_candidates)


class EvidenceFactAssessmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_validated_fact_can_complete_metric_and_period_coverage(self) -> None:
        runner = EvidenceAssessmentRunner(
            metadata_repository=_MetadataRepository(),
            cache=_ArtifactCache(),
            resolver=_NeverAmbiguityResolver(),
        )
        retrieval = RetrievalResult(
            retrieval_scope="normal",
            table_intent="required",
            signals=RetrievalSignals(),
            text_evidence=(_chunk(),),
        )
        outcome = await runner.run(
            request=AnalysisRequest(
                user_id="user-1",
                chat_id="chat-1",
                query="What was total revenue in 2023?",
                document_ids=(DOCUMENT_ID,),
            ),
            requirements=_requirements(),
            retrieval=retrieval,
            evidence=EvidencePackage(
                run_id="run-1",
                status="empty",
                retrieved_table_count=0,
                hydrated_table_count=0,
            ),
            profiles=DatasetProfiles(
                profiler_version="test-profiler",
                status="empty",
                requested_count=0,
                profiled_count=0,
                cache_hit_count=0,
                generated_count=0,
            ),
            facts=(_fact(),),
        )

        self.assertEqual(outcome.artifact.decision, ReadinessDecision.READY)
        self.assertEqual(outcome.artifact.supported_count, 2)
        self.assertEqual(
            outcome.artifact.document_coverage[0].fact_ids,
            ("fact_111111111111111111111111",),
        )
        self.assertTrue(
            all(
                item.evidence[0].evidence_kind.value == "fact"
                for item in outcome.artifact.coverage
            )
        )


class _NoopEvidenceRepository:
    async def load_sources(self, **_kwargs: Any) -> Any:
        raise AssertionError("no table candidate should be hydrated")


class _RescueEvidenceRepository:
    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table
        self.calls = 0

    async def load_sources(self, **_kwargs: Any) -> HydrationSourceBatch:
        self.calls += 1
        return HydrationSourceBatch(
            tables=(self.table,),
            documents=(
                {
                    "document_id": DOCUMENT_ID,
                    "filename": "report.pdf",
                    "pages": 50,
                    "ingestion_status": "ready",
                    "table_ingestion_status": "ready",
                },
            ),
        )


class _DatasetRepository:
    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table

    async def load_tables(self, **_kwargs: Any) -> tuple[dict[str, Any], ...]:
        return (self.table,)


class _ProfileCache:
    async def load_many(self, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def save_many(self, **_kwargs: Any) -> None:
        return None


class _NoopProfilingRunner:
    async def run(self, **_kwargs: Any) -> Any:
        raise AssertionError("no dataset should be profiled")


class _FactTextService:
    def __init__(self, fact: EvidenceFact | None) -> None:
        self.fact = fact
        self.calls = 0

    async def run(self, **_kwargs: Any) -> TextCompletionOutcome:
        self.calls += 1
        return TextCompletionOutcome(
            facts=(self.fact,) if self.fact else (),
            attempts=(
                CompletionAttempt(
                    attempt_id=f"text_{self.calls}",
                    stage=CompletionStage.EXISTING_TEXT_EXTRACTION,
                    outcome=(
                        CompletionAttemptOutcome.EVIDENCE_ADDED
                        if self.fact
                        else CompletionAttemptOutcome.NO_MATCH
                    ),
                    accepted_fact_count=1 if self.fact else 0,
                    reason="test",
                ),
            ),
        )


class _RepairRetriever:
    def __init__(self) -> None:
        self.calls = 0

    async def retrieve(self, *, attempt: int, **_kwargs: Any) -> TargetedRepairResult:
        self.calls += 1
        return TargetedRepairResult(
            queries=(f"repair query {attempt}",),
            document_ids=(DOCUMENT_ID,),
            table_candidates=(),
            text_evidence=(),
        )


class EvidenceCompletionRunnerTests(unittest.IsolatedAsyncioTestCase):
    def _runner(
        self,
        *,
        text_service: _FactTextService,
        repair: _RepairRetriever,
    ) -> EvidenceCompletionRunner:
        return EvidenceCompletionRunner(
            evidence_repository=_NoopEvidenceRepository(),
            profiling_runner=_NoopProfilingRunner(),
            assessment_runner=EvidenceAssessmentRunner(
                metadata_repository=_MetadataRepository(),
                cache=_ArtifactCache(),
                resolver=_NeverAmbiguityResolver(),
            ),
            text_service=text_service,
            repair_retriever=repair,
        )

    async def test_existing_text_fact_stops_before_retrieval_repair(self) -> None:
        repair = _RepairRetriever()
        text_service = _FactTextService(_fact())
        runner = self._runner(text_service=text_service, repair=repair)
        evidence = EvidencePackage(
            run_id="run-1",
            status="empty",
            retrieved_table_count=0,
            hydrated_table_count=0,
        )
        profiles = DatasetProfiles(
            profiler_version="test-profiler",
            status="empty",
            requested_count=0,
            profiled_count=0,
            cache_hit_count=0,
            generated_count=0,
        )

        outcome = await runner.run(
            run_id="run-1",
            request=AnalysisRequest(
                user_id="user-1",
                chat_id="chat-1",
                query="What was total revenue in 2023?",
                document_ids=(DOCUMENT_ID,),
            ),
            requirements=_requirements(),
            retrieval=RetrievalResult(
                retrieval_scope="normal",
                table_intent="required",
                signals=RetrievalSignals(),
                text_evidence=(_chunk(),),
            ),
            evidence=evidence,
            profiles=profiles,
            assessment=_assessment(),
        )

        self.assertEqual(outcome.artifact.status, CompletionStatus.READY)
        self.assertEqual(outcome.assessment.decision, ReadinessDecision.READY)
        self.assertEqual(len(outcome.artifact.facts), 1)
        self.assertEqual(outcome.artifact.added_datasets, ())
        self.assertEqual(repair.calls, 0)
        self.assertEqual(evidence.datasets, ())

    async def test_unused_table_candidate_is_hydrated_profiled_and_reassessed(
        self,
    ) -> None:
        raw_table = {
            "table_id": "table-revenue",
            "document_id": DOCUMENT_ID,
            "user_id": "user-1",
            "page_start": 44,
            "page_end": 44,
            "title": "Total revenue by year",
            "extraction_method": "pymupdf",
            "columns": [
                {"key": "metric", "label": "Metric", "type": "string"},
                {
                    "key": "2023",
                    "label": "2023",
                    "type": "number",
                    "unit": "USD million",
                },
            ],
            "rows": [{"metric": "Total revenue", "2023": 165.8}],
            "source_fragments": [
                {
                    "page": 44,
                    "bounding_box": [10.0, 20.0, 500.0, 700.0],
                }
            ],
        }
        repository = _RescueEvidenceRepository(raw_table)
        repair = _RepairRetriever()
        text_service = _FactTextService(None)
        runner = EvidenceCompletionRunner(
            evidence_repository=repository,
            profiling_runner=DatasetProfilingRunner(
                dataset_repository=_DatasetRepository(raw_table),
                profile_cache=_ProfileCache(),
            ),
            assessment_runner=EvidenceAssessmentRunner(
                metadata_repository=_MetadataRepository(),
                cache=_ArtifactCache(),
                resolver=_NeverAmbiguityResolver(),
            ),
            text_service=text_service,
            repair_retriever=repair,
        )
        missing_assessment = EvidenceAssessment(
            ambiguity_model="test-ambiguity",
            decision=ReadinessDecision.NEEDS_CANDIDATE_RESCUE,
            coverage=(
                RequirementCoverage(
                    requirement_id="req_metric_total_revenue",
                    status=CoverageStatus.MISSING,
                    confidence=0,
                    reason="Missing.",
                ),
                RequirementCoverage(
                    requirement_id="req_period_2023",
                    status=CoverageStatus.MISSING,
                    confidence=0,
                    reason="Missing.",
                ),
            ),
            document_coverage=(
                DocumentCoverage(
                    document_id=DOCUMENT_ID,
                    document_name="report.pdf",
                    required=True,
                    status=CoverageStatus.MISSING,
                ),
            ),
            required_count=2,
            supported_count=0,
            partial_count=0,
            missing_count=2,
            conflicting_count=0,
            ambiguous_count=0,
        )

        outcome = await runner.run(
            run_id="run-1",
            request=AnalysisRequest(
                user_id="user-1",
                chat_id="chat-1",
                query="What was total revenue in 2023?",
                document_ids=(DOCUMENT_ID,),
            ),
            requirements=_requirements(),
            retrieval=RetrievalResult(
                retrieval_scope="normal",
                table_intent="required",
                signals=RetrievalSignals(),
                table_candidates=(
                    TableCandidateReference(
                        table_id="table-revenue",
                        document_id=DOCUMENT_ID,
                        title="Total revenue by year",
                        summary="Total revenue for 2023",
                        expected_columns=("metric", "2023"),
                        expected_metrics=("total revenue",),
                        expected_units=("USD million",),
                        page_start=44,
                        page_end=44,
                    ),
                ),
            ),
            evidence=EvidencePackage(
                run_id="run-1",
                status="empty",
                retrieved_table_count=0,
                hydrated_table_count=0,
            ),
            profiles=DatasetProfiles(
                profiler_version=DATASET_PROFILER_VERSION,
                status="empty",
                requested_count=0,
                profiled_count=0,
                cache_hit_count=0,
                generated_count=0,
            ),
            assessment=missing_assessment,
        )

        self.assertEqual(outcome.assessment.decision, ReadinessDecision.READY)
        self.assertEqual(len(outcome.artifact.added_datasets), 1)
        self.assertEqual(
            outcome.artifact.added_datasets[0].dataset.table_id,
            "table-revenue",
        )
        self.assertIsNotNone(outcome.artifact.additional_profiles)
        self.assertEqual(repository.calls, 1)
        self.assertEqual(text_service.calls, 0)
        self.assertEqual(repair.calls, 0)

    async def test_targeted_retrieval_is_bounded_to_two_attempts(self) -> None:
        repair = _RepairRetriever()
        runner = self._runner(
            text_service=_FactTextService(None),
            repair=repair,
        )

        outcome = await runner.run(
            run_id="run-1",
            request=AnalysisRequest(
                user_id="user-1",
                chat_id="chat-1",
                query="What was total revenue in 2023?",
                document_ids=(DOCUMENT_ID,),
            ),
            requirements=_requirements(),
            retrieval=RetrievalResult(
                retrieval_scope="normal",
                table_intent="required",
                signals=RetrievalSignals(),
            ),
            evidence=EvidencePackage(
                run_id="run-1",
                status="empty",
                retrieved_table_count=0,
                hydrated_table_count=0,
            ),
            profiles=DatasetProfiles(
                profiler_version="test-profiler",
                status="empty",
                requested_count=0,
                profiled_count=0,
                cache_hit_count=0,
                generated_count=0,
            ),
            assessment=_assessment(
                decision=ReadinessDecision.NEEDS_RETRIEVAL_REPAIR
            ),
        )

        self.assertEqual(repair.calls, 2)
        self.assertEqual(outcome.assessment.decision, ReadinessDecision.UNANSWERABLE)
        self.assertEqual(outcome.artifact.status, CompletionStatus.EXHAUSTED)
        self.assertEqual(
            len(
                [
                    item
                    for item in outcome.artifact.attempts
                    if item.stage == CompletionStage.TARGETED_RETRIEVAL
                ]
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
