from __future__ import annotations

import unittest
from typing import Any

from scripts.data_analysis_agent.analysis.graph import build_data_analysis_graph
from scripts.data_analysis_agent.analysis.models import (
    AnalysisOperation,
    CompletionStatus,
    ExpectedDataType,
    ExtractedRequirement,
    ReadinessDecision,
    RequirementKind,
    RequirementsExtraction,
)
from scripts.data_analysis_agent.analysis.services import (
    EvidenceCompletionRunner,
    RequirementsExtractor,
)
from scripts.data_analysis_agent.analysis.state import (
    AnalysisPhase,
    create_analysis_state,
)
from scripts.data_analysis_agent.runtime.models import TabularDataset
from tests.test_data_analysis_parent_graph import (
    _FakeArtifactCache,
    _FakeAssessmentMetadataRepository,
    _FakeEvidenceRepository,
    _FakeNormalizedDatasetRepository,
    _FakeProfileCache,
    _FakeRequirementsGenerator,
    _FakeRetrievalGraph,
    _NoopCompletionRunner,
)
from tests.test_data_analysis_phase7_runtime_adapter import _dataset_handle


class _SpreadsheetDatasetRepository:
    def __init__(self, dataset: TabularDataset) -> None:
        self.dataset = dataset
        self.calls: list[dict[str, Any]] = []

    async def load_datasets(self, **kwargs: Any) -> tuple[TabularDataset, ...]:
        self.calls.append(kwargs)
        requested = {
            item.dataset_id for item in kwargs.get("datasets", ())
        }
        return (self.dataset,) if self.dataset.dataset_id in requested else ()


class _MissingColumnRequirementsGenerator:
    async def ainvoke(self, _input: Any, **_kwargs: Any) -> RequirementsExtraction:
        return RequirementsExtraction(
            operation=AnalysisOperation.LOOKUP,
            requirements=(
                ExtractedRequirement(
                    kind=RequirementKind.METRIC,
                    name="operating profit",
                    expected_data_type=ExpectedDataType.NUMBER,
                ),
            ),
            table_evidence_required=True,
            text_evidence_acceptable=False,
        )


class _NeverCompletionDependency:
    async def run(self, **_kwargs: Any) -> Any:
        raise AssertionError(
            "spreadsheet-only completion must not use PDF dependencies"
        )

    async def load_sources(self, **_kwargs: Any) -> Any:
        raise AssertionError(
            "spreadsheet-only completion must not hydrate PDF candidates"
        )


class _TextCompletionSpy:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError(
            "spreadsheet-only completion must not extract PDF text"
        )


class _RepairRetrieverSpy:
    def __init__(self) -> None:
        self.calls = 0

    async def retrieve(self, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError(
            "spreadsheet-only completion must not query PDF vectors"
        )


class SpreadsheetParentGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_range_uses_shared_phase1_to7_pipeline(self) -> None:
        handle = _dataset_handle()
        tabular = TabularDataset(
            dataset_id=handle.dataset_id,
            user_id=handle.user_id,
            workspace_id=handle.workspace_id,
            source_type=handle.source_type,
            source_version=handle.source_version,
            title=handle.title,
            columns=handle.columns,
            rows=({"revenue": 60_000}, {"revenue": 49_000}),
            locator=handle.locator,
        )
        retrieval = _FakeRetrievalGraph(fail=True)
        evidence_repository = _FakeEvidenceRepository()
        dataset_repository = _SpreadsheetDatasetRepository(tabular)
        graph = build_data_analysis_graph(
            retrieval_graph=retrieval,
            evidence_repository=evidence_repository,
            dataset_repository=dataset_repository,
            profile_cache=_FakeProfileCache(),
            requirements_cache=_FakeArtifactCache(),
            requirements_extractor=RequirementsExtractor(
                _FakeRequirementsGenerator(),
                model="test-requirements-model",
            ),
            assessment_metadata_repository=(
                _FakeAssessmentMetadataRepository()
            ),
            assessment_cache=_FakeArtifactCache(),
            completion_runner=_NoopCompletionRunner(),
            normalized_dataset_repository=(
                _FakeNormalizedDatasetRepository()
            ),
        )
        state = create_analysis_state(
            user_id=handle.user_id,
            workspace_id=handle.workspace_id,
            chat_id="chat-1",
            query="compare revenue",
            pinned_datasets=(handle,),
        )

        result = await graph.ainvoke(state)

        self.assertEqual(result["phase"], AnalysisPhase.PREPARED)
        self.assertFalse(retrieval.inputs)
        self.assertFalse(evidence_repository.calls)
        self.assertGreaterEqual(len(dataset_repository.calls), 2)
        evidence = result["evidence_package"].datasets[0]
        self.assertEqual(evidence.source_type, "spreadsheet_range")
        self.assertEqual(evidence.artifact_id, "workbook-1")
        self.assertEqual(evidence.range_a1, "Sheet1!A1:A2")
        self.assertNotIn("rows", evidence.model_dump())
        normalization = result["normalization_result"]
        self.assertTrue(normalization.can_analyze)
        self.assertEqual(
            normalization.datasets[0].source_type,
            "spreadsheet_range",
        )
        self.assertNotIn("rows", normalization.model_dump())

    async def test_missing_spreadsheet_column_requests_clarification_without_pdf_repair(
        self,
    ) -> None:
        handle = _dataset_handle()
        tabular = TabularDataset(
            dataset_id=handle.dataset_id,
            user_id=handle.user_id,
            workspace_id=handle.workspace_id,
            source_type=handle.source_type,
            source_version=handle.source_version,
            title=handle.title,
            columns=handle.columns,
            rows=({"revenue": 60_000}, {"revenue": 49_000}),
            locator=handle.locator,
        )
        retrieval = _FakeRetrievalGraph(fail=True)
        evidence_repository = _FakeEvidenceRepository()
        dataset_repository = _SpreadsheetDatasetRepository(tabular)
        text_spy = _TextCompletionSpy()
        repair_spy = _RepairRetrieverSpy()
        unavailable = _NeverCompletionDependency()
        completion_runner = EvidenceCompletionRunner(
            evidence_repository=unavailable,
            profiling_runner=unavailable,
            assessment_runner=unavailable,
            text_service=text_spy,
            repair_retriever=repair_spy,
        )
        graph = build_data_analysis_graph(
            retrieval_graph=retrieval,
            evidence_repository=evidence_repository,
            dataset_repository=dataset_repository,
            profile_cache=_FakeProfileCache(),
            requirements_cache=_FakeArtifactCache(),
            requirements_extractor=RequirementsExtractor(
                _MissingColumnRequirementsGenerator(),
                model="test-requirements-model",
            ),
            assessment_metadata_repository=(
                _FakeAssessmentMetadataRepository()
            ),
            assessment_cache=_FakeArtifactCache(),
            completion_runner=completion_runner,
            normalized_dataset_repository=(
                _FakeNormalizedDatasetRepository()
            ),
        )
        state = create_analysis_state(
            user_id=handle.user_id,
            workspace_id=handle.workspace_id,
            chat_id="chat-1",
            query="Show operating profit",
            pinned_datasets=(handle,),
        )

        result = await graph.ainvoke(state)

        self.assertEqual(result["phase"], AnalysisPhase.COMPLETED)
        self.assertEqual(
            result["evidence_assessment"].decision,
            ReadinessDecision.NEEDS_CLARIFICATION,
        )
        self.assertEqual(
            result["augmented_evidence"].status,
            CompletionStatus.SKIPPED,
        )
        self.assertEqual(result["augmented_evidence"].attempts, ())
        self.assertEqual(text_spy.calls, 0)
        self.assertEqual(repair_spy.calls, 0)
        self.assertFalse(retrieval.inputs)
        self.assertFalse(evidence_repository.calls)


if __name__ == "__main__":
    unittest.main()
