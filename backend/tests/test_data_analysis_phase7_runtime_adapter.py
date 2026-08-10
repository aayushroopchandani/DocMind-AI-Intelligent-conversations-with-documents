from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from scripts.data_analysis_agent.analysis.models import (
    AnalysisIssue,
    AnalysisOperation,
    AnalysisRequirements,
    AssessmentDiagnostics,
    DatasetProfiles,
    EvidenceAssessment,
    EvidencePackage,
    IssueCode,
    IssueSeverity,
    IssueStage,
    MaterializationType,
    NormalizationResult,
    NormalizedDatasetReference,
    ReadinessDecision,
    RequirementItem,
    RequirementsDiagnostics,
    RetrievalResult,
    RetrievalSignals,
)
from scripts.data_analysis_agent.analysis.state import AnalysisPhase
from scripts.data_analysis_agent.runtime.integration import (
    Phase7AnalysisAdapter,
    Phase7ExecutionCancelled,
    Phase7InputError,
    Phase7Progress,
)
from scripts.data_analysis_agent.runtime.models import (
    AnalysisEventType,
    AnalysisMode,
    AnalysisRun,
    AnalysisRunOutcome,
    BlobDatasetStorage,
    BlobDeliveryType,
    BlobProvider,
    BlobReference,
    BlobResourceType,
    DatasetColumn,
    DatasetColumnType,
    DatasetHandle,
    DatasetSourceType,
    DatasetVersionReference,
    SpreadsheetRangeLocator,
)
from tests.test_data_analysis_requirements_assessment import (
    _evidence_and_profiles,
    _raw_table,
)


_HASH = "1" * 64
_NORMALIZED_ID = f"normalized_{'a' * 24}"


class _FakeGraph:
    def __init__(
        self,
        states: tuple[Mapping[str, Any], ...],
        *,
        error: Exception | None = None,
    ) -> None:
        self.states = states
        self.error = error
        self.input: Mapping[str, Any] | None = None
        self.config: Mapping[str, Any] | None = None
        self.stream_mode: str | None = None
        self.yield_count = 0

    async def astream(
        self,
        input: Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
        *,
        stream_mode: str,
        **_kwargs: Any,
    ) -> Any:
        self.input = input
        self.config = config
        self.stream_mode = stream_mode
        for state in self.states:
            self.yield_count += 1
            yield state
        if self.error is not None:
            raise self.error


class _Reporter:
    def __init__(self) -> None:
        self.events: list[Phase7Progress] = []

    async def emit(self, progress: Phase7Progress) -> None:
        self.events.append(progress)


def _dataset_handle(
    *,
    user_id: str = "user-1",
    workspace_id: str = "workspace-1",
    source_version: str = _HASH,
) -> DatasetHandle:
    blob = BlobReference(
        provider=BlobProvider.CLOUDINARY,
        object_key="analysis/user-1/workspace-1/dataset.json.gz",
        provider_asset_id="asset-1",
        provider_version="1",
        resource_type=BlobResourceType.RAW,
        delivery_type=BlobDeliveryType.AUTHENTICATED,
        content_type="application/gzip",
        filename="dataset.json.gz",
        byte_count=100,
        sha256=source_version,
    )
    return DatasetHandle(
        dataset_id="dataset-sheet-1",
        user_id=user_id,
        workspace_id=workspace_id,
        source_type=DatasetSourceType.SPREADSHEET_RANGE,
        source_version=source_version,
        content_hash=source_version,
        title="Revenue",
        columns=(
            DatasetColumn(
                key="revenue",
                label="Revenue",
                type=DatasetColumnType.NUMBER,
                source_index=0,
            ),
        ),
        row_count=2,
        locator=SpreadsheetRangeLocator(
            artifact_id="workbook-1",
            artifact_version_id="workbook-version-1",
            workbook_id="workbook-1",
            workbook_revision=12,
            worksheet_id="sheet-1",
            worksheet_name="Sheet1",
            range_a1="Sheet1!A1:A2",
            snapshot_hash=source_version,
        ),
        storage=BlobDatasetStorage(
            artifact_version_id="dataset-version-1",
            blob=blob,
            encoding="tabular_json_gzip",
        ),
    )


def _run(
    *,
    dataset: DatasetHandle | None = None,
    user_id: str = "user-1",
    workspace_id: str = "workspace-1",
) -> AnalysisRun:
    versions = (
        (
            DatasetVersionReference(
                dataset_id=dataset.dataset_id,
                source_version=dataset.source_version,
            ),
        )
        if dataset is not None
        else ()
    )
    return AnalysisRun(
        run_id=str(uuid4()),
        user_id=user_id,
        workspace_id=workspace_id,
        chat_id="chat-1",
        idempotency_key="request-key-1",
        request_fingerprint="f" * 64,
        mode=AnalysisMode.ANALYSE,
        prompt="Filter rows where revenue is greater than 50,000",
        input_dataset_versions=versions,
        selected_document_ids=(() if dataset is not None else ("2" * 64,)),
    )


def _retrieval() -> RetrievalResult:
    return RetrievalResult(
        retrieval_scope="normal",
        table_intent="required",
        signals=RetrievalSignals(),
        table_references=(),
    )


def _requirements() -> AnalysisRequirements:
    return AnalysisRequirements.model_construct(
        model="test-model",
        operation=AnalysisOperation.LOOKUP,
        selected_document_ids=("workbook-1",),
        requirements=(RequirementItem.model_construct(required=True),),
        diagnostics=RequirementsDiagnostics(),
    )


def _assessment(decision: ReadinessDecision) -> EvidenceAssessment:
    return EvidenceAssessment.model_construct(
        decision=decision,
        required_count=1,
        supported_count=1 if decision == ReadinessDecision.READY else 0,
        partial_count=0,
        missing_count=0 if decision == ReadinessDecision.READY else 1,
        conflicting_count=0,
        ambiguous_count=0,
        diagnostics=AssessmentDiagnostics(),
    )


def _normalization() -> NormalizationResult:
    dataset = NormalizedDatasetReference.model_construct(
        normalized_dataset_id=_NORMALIZED_ID,
        source_dataset_ids=("dataset-sheet-1",),
        materialization=MaterializationType.SOURCE_PASSTHROUGH,
        output_row_count=2,
        output_column_count=1,
        cache_hit=False,
    )
    return NormalizationResult.model_construct(
        status="ready",
        datasets=(dataset,),
        selected_fact_ids=(),
        selected_derived_dataset_ids=(),
        non_tabular_requirement_ids=(),
        prepared_dataset_count=1,
        total_input_rows=2,
        total_output_rows=2,
        can_analyze=True,
    )


class Phase7RuntimeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepared_pdf_sources_are_exported_as_durable_handles(self) -> None:
        item = _raw_table(
            table_id="pdf-table-1",
            document_id="2" * 64,
            title="Income statement",
            document_name="annual-report.pdf",
            columns=[
                {"key": "metric", "label": "Metric", "type": "string"},
                {"key": "value", "label": "Value", "type": "number"},
            ],
            rows=[{"metric": "Revenue", "value": 100}],
        )
        evidence, profiles, _metadata = _evidence_and_profiles(item)
        reference = item[0]
        normalization = NormalizationResult.model_construct(
            status="ready",
            datasets=(
                NormalizedDatasetReference.model_construct(
                    normalized_dataset_id=_NORMALIZED_ID,
                    source_dataset_ids=(reference.dataset_id,),
                    source_versions=(reference.source_version,),
                    materialization=MaterializationType.SOURCE_PASSTHROUGH,
                    output_row_count=1,
                    output_column_count=2,
                    cache_hit=False,
                ),
            ),
            selected_fact_ids=(),
            selected_derived_dataset_ids=(),
            non_tabular_requirement_ids=(),
            prepared_dataset_count=1,
            total_input_rows=1,
            total_output_rows=1,
            can_analyze=True,
        )
        graph = _FakeGraph(
            (
                {
                    "phase": AnalysisPhase.PREPARED,
                    "retrieval_result": _retrieval(),
                    "analysis_requirements": _requirements(),
                    "evidence_package": evidence,
                    "dataset_profiles": profiles,
                    "evidence_assessment": _assessment(ReadinessDecision.READY),
                    "normalization_result": normalization,
                    "warnings": [],
                    "errors": [],
                },
            )
        )

        result = await Phase7AnalysisAdapter(graph).execute(_run())

        handles = result.planning_artifacts.source_dataset_handles
        self.assertEqual(len(handles), 1)
        self.assertEqual(handles[0].dataset_id, reference.dataset_id)
        self.assertEqual(handles[0].source_version, reference.source_version)
        self.assertEqual(handles[0].storage.collection, "structured_tables")
        self.assertEqual(handles[0].workspace_id, "workspace-1")

    async def test_prepared_run_streams_bounded_milestones(self) -> None:
        dataset = _dataset_handle()
        graph = _FakeGraph(
            (
                {
                    "phase": AnalysisPhase.PREPARED,
                    "retrieval_result": _retrieval(),
                    "analysis_requirements": _requirements(),
                    "evidence_package": EvidencePackage.model_construct(
                        status="complete",
                        retrieved_table_count=1,
                        hydrated_table_count=1,
                        unresolved_tables=(),
                    ),
                    "dataset_profiles": DatasetProfiles.model_construct(
                        status="complete",
                        requested_count=1,
                        profiled_count=1,
                        failures=(),
                        cache_hit_count=0,
                    ),
                    "evidence_assessment": _assessment(
                        ReadinessDecision.READY
                    ),
                    "normalization_result": _normalization(),
                    "warnings": [],
                    "errors": [],
                },
            )
        )
        reporter = _Reporter()

        result = await Phase7AnalysisAdapter(graph).execute(
            _run(dataset=dataset),
            (dataset,),
            reporter,
        )

        self.assertEqual(result.outcome, AnalysisRunOutcome.DATASETS_PREPARED)
        self.assertEqual(result.final_dataset_ids, (_NORMALIZED_ID,))
        self.assertEqual(result.source_dataset_ids, ("dataset-sheet-1",))
        self.assertEqual(result.total_output_rows, 2)
        self.assertEqual(graph.stream_mode, "values")
        self.assertEqual(
            graph.config["configurable"]["thread_id"],
            graph.input["run_id"],
        )
        request = graph.input["request"]
        self.assertEqual(request.workspace_id, "workspace-1")
        self.assertEqual(request.chat_id, "chat-1")
        self.assertEqual(request.pinned_datasets, (dataset,))

        event_types = [event.event_type for event in reporter.events]
        self.assertEqual(
            event_types,
            [
                AnalysisEventType.RETRIEVAL_STARTED,
                AnalysisEventType.REQUIREMENTS_STARTED,
                AnalysisEventType.RETRIEVAL_COMPLETED,
                AnalysisEventType.REQUIREMENTS_COMPLETED,
                AnalysisEventType.EVIDENCE_HYDRATED,
                AnalysisEventType.DATASETS_PROFILED,
                AnalysisEventType.EVIDENCE_ASSESSED,
                AnalysisEventType.DATASET_PREPARED,
            ],
        )
        self.assertEqual(
            len({event.deduplication_key for event in reporter.events}),
            len(reporter.events),
        )
        serialized_events = " ".join(
            event.model_dump_json() for event in reporter.events
        )
        self.assertNotIn("50,000", serialized_events)
        self.assertNotIn("rows", serialized_events.casefold())

    async def test_final_readiness_decisions_are_mapped_honestly(self) -> None:
        run = _run()
        cases = (
            (
                AnalysisPhase.ASSESSED,
                ReadinessDecision.NEEDS_CLARIFICATION,
                AnalysisRunOutcome.CLARIFICATION_REQUIRED,
            ),
            (
                AnalysisPhase.COMPLETED,
                ReadinessDecision.UNANSWERABLE,
                AnalysisRunOutcome.UNANSWERABLE,
            ),
        )
        for phase, decision, expected in cases:
            with self.subTest(decision=decision):
                graph = _FakeGraph(
                    (
                        {
                            "phase": phase,
                            "evidence_assessment": _assessment(decision),
                            "warnings": [],
                            "errors": [],
                        },
                    )
                )
                result = await Phase7AnalysisAdapter(graph).execute(run=run)
                self.assertEqual(result.outcome, expected)
                self.assertEqual(result.final_dataset_ids, ())

    async def test_failed_graph_state_preserves_issue_summary(self) -> None:
        graph = _FakeGraph(
            (
                {
                    "phase": AnalysisPhase.FAILED,
                    "warnings": [],
                    "errors": [
                        AnalysisIssue(
                            code=IssueCode.RETRIEVAL_FAILED,
                            severity=IssueSeverity.ERROR,
                            stage=IssueStage.RETRIEVAL,
                            message="Evidence retrieval failed.",
                            retryable=True,
                        )
                    ],
                },
            )
        )

        result = await Phase7AnalysisAdapter(graph).execute(run=_run())

        self.assertEqual(result.outcome, AnalysisRunOutcome.FAILED)
        self.assertEqual(result.errors[0].code, "retrieval_failed")
        self.assertTrue(result.errors[0].retryable)

    async def test_unexpected_graph_exception_becomes_retryable_failure(self) -> None:
        graph = _FakeGraph((), error=RuntimeError("provider secret"))

        result = await Phase7AnalysisAdapter(graph).execute(run=_run())

        self.assertEqual(result.outcome, AnalysisRunOutcome.FAILED)
        self.assertEqual(
            result.errors[0].code,
            "phase7_graph_execution_failed",
        )
        self.assertTrue(result.errors[0].retryable)
        self.assertNotIn("provider secret", result.errors[0].message)

    async def test_cancellation_is_checked_after_each_graph_update(self) -> None:
        graph = _FakeGraph(
            (
                {
                    "phase": AnalysisPhase.RETRIEVED,
                    "retrieval_result": _retrieval(),
                },
                {
                    "phase": AnalysisPhase.ASSESSED,
                    "evidence_assessment": _assessment(
                        ReadinessDecision.UNANSWERABLE
                    ),
                },
            )
        )

        async def cancelled() -> bool:
            return graph.yield_count >= 1

        with self.assertRaises(Phase7ExecutionCancelled):
            await Phase7AnalysisAdapter(graph).execute(
                run=_run(),
                is_cancelled=cancelled,
            )

        self.assertEqual(graph.yield_count, 1)

    async def test_pinned_versions_and_tenant_scope_are_enforced(self) -> None:
        dataset = _dataset_handle()
        empty_graph = _FakeGraph(())
        with self.assertRaises(Phase7InputError):
            await Phase7AnalysisAdapter(empty_graph).execute(
                run=_run(dataset=dataset),
                datasets=(
                    _dataset_handle(source_version="3" * 64),
                ),
            )
        with self.assertRaises(Phase7InputError):
            await Phase7AnalysisAdapter(empty_graph).execute(
                run=_run(dataset=dataset),
                datasets=(
                    _dataset_handle(user_id="another-user"),
                ),
            )


if __name__ == "__main__":
    unittest.main()
