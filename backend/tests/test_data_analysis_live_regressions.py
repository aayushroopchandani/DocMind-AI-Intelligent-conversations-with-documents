from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.data_analysis_agent.extraction.docling_fallback import _run_worker
from scripts.data_analysis_agent.extraction.utils.table_coverage_detector import (
    PageRange,
)
from scripts.data_analysis_agent.analysis.models import (
    AnalysisOperation,
    AnalysisRequirements,
    RequirementItem,
    RequirementKind,
)
from scripts.data_analysis_agent.analysis.services import EvidenceAssessmentRunner
from scripts.data_analysis_agent.analysis.services.preparation.selection import (
    select_preparation_evidence,
)
from scripts.data_analysis_agent.runtime.models import DatasetCatalogEntry
from scripts.data_analysis_agent.runtime.models.artifacts import (
    ArtifactVersion,
    ArtifactVersionStatus,
)
from scripts.data_analysis_agent.runtime.repositories.artifacts import (
    MongoArtifactRepository,
)
from scripts.data_analysis_agent.runtime.repositories.datasets import (
    MongoDatasetCatalogRepository,
)
from tests.test_data_analysis_phase7_runtime_adapter import _dataset_handle
from tests.test_data_analysis_requirements_assessment import (
    DOCUMENT_A,
    _ArtifactCache,
    _MetadataRepository,
    _evidence_and_profiles,
    _raw_table,
    _request,
    _retrieval,
)


class _CatalogCollection:
    def __init__(self, document: dict[str, object]) -> None:
        self.document = document

    async def update_one(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def find_one(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return self.document


class _Database:
    def __init__(self, collection: _CatalogCollection) -> None:
        self.collection = collection

    def __getitem__(self, _name: str) -> _CatalogCollection:
        return self.collection


class _SuccessfulProcess:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


class LiveIntegrationRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_dataset_registration_ignores_bson_timestamp_precision(self) -> None:
        handle = _dataset_handle()
        entry = DatasetCatalogEntry(handle=handle)
        stored = entry.model_dump(mode="python")
        stored_handle = dict(stored["handle"])
        created_at = handle.created_at.replace(
            microsecond=(handle.created_at.microsecond // 1000) * 1000,
            tzinfo=None,
        )
        stored_handle["created_at"] = created_at
        stored["handle"] = stored_handle
        stored["registered_at"] = entry.registered_at.replace(tzinfo=None)
        stored["updated_at"] = entry.updated_at.replace(tzinfo=None)
        database = _Database(_CatalogCollection(stored))

        with patch(
            "scripts.data_analysis_agent.runtime.repositories.datasets.get_db",
            return_value=database,
        ):
            registered = await MongoDatasetCatalogRepository().register(entry)

        self.assertEqual(registered.handle.dataset_id, handle.dataset_id)
        self.assertEqual(registered.handle.created_at.tzinfo, timezone.utc)

    async def test_docling_worker_runs_from_backend_package_root(self) -> None:
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def create_process(
            *args: object,
            **kwargs: object,
        ) -> _SuccessfulProcess:
            calls.append((args, kwargs))
            output_index = args.index("--output") + 1
            Path(str(args[output_index])).write_text("[]", encoding="utf-8")
            return _SuccessfulProcess()

        with patch(
            "scripts.data_analysis_agent.extraction.docling_fallback."
            "asyncio.create_subprocess_exec",
            side_effect=create_process,
        ):
            tables = await _run_worker(
                Path("report.pdf"),
                (PageRange(page_start=1, page_end=1, flagged_pages=[1]),),
            )

        self.assertEqual(tables, [])
        args, kwargs = calls[0]
        backend_root = Path(__file__).resolve().parents[1]
        self.assertEqual(Path(str(kwargs["cwd"])), backend_root)
        self.assertEqual(
            Path(str(kwargs["env"]["PYTHONPATH"].split(os.pathsep, 1)[0])),
            backend_root,
        )
        self.assertIn("scripts.data_analysis_agent.extraction.docling_worker", args)

    async def test_source_independent_request_keeps_profiled_source_context(
        self,
    ) -> None:
        item = _raw_table(
            table_id="source-context-1",
            document_id=DOCUMENT_A,
            title="Current sheet",
            document_name="workbook.xlsx",
            columns=[{"key": "revenue", "label": "Revenue", "type": "number"}],
            rows=[{"revenue": 10}],
        )
        evidence, profiles, metadata = _evidence_and_profiles(item)
        requirements = AnalysisRequirements(
            model="test-model",
            operation=AnalysisOperation.OTHER,
            selected_document_ids=(DOCUMENT_A,),
            requirements=(
                RequirementItem(
                    requirement_id="req_topic_synthetic_data",
                    kind=RequirementKind.TOPIC,
                    name="synthetic data",
                    required=False,
                ),
            ),
            source_evidence_required=False,
        )
        runner = EvidenceAssessmentRunner(
            metadata_repository=_MetadataRepository(metadata),
            cache=_ArtifactCache(),
        )

        assessment = (
            await runner.run(
                request=_request("Generate reproducible synthetic data."),
                requirements=requirements,
                retrieval=_retrieval(),
                evidence=evidence,
                profiles=profiles,
            )
        ).artifact
        selection = select_preparation_evidence(
            requirements=requirements,
            assessment=assessment,
            evidence=evidence,
            profiles=profiles,
        )

        self.assertEqual(assessment.decision.value, "ready")
        self.assertEqual(
            tuple(value.dataset.dataset_id for value in selection.datasets),
            (item[0].dataset_id,),
        )
        self.assertEqual(selection.datasets[0].requirement_ids, ())


class MongoDatetimeRegressionTests(unittest.TestCase):
    def test_artifact_parser_normalizes_naive_lease_datetime(self) -> None:
        now = datetime.now(timezone.utc)
        version = ArtifactVersion(
            version_id="version-1",
            artifact_id="artifact-1",
            user_id="user-1",
            workspace_id="workspace-1",
            version_number=1,
            content_hash="a" * 64,
            byte_count=10,
            content_type="application/json",
            filename="snapshot.json",
            status=ArtifactVersionStatus.UPLOADING,
            upload_owner_id="worker-1",
            upload_lease_expires_at=now + timedelta(minutes=1),
            upload_attempt=1,
        )
        document = version.model_dump(mode="python")
        document["created_at"] = version.created_at.replace(tzinfo=None)
        document["updated_at"] = version.updated_at.replace(tzinfo=None)
        document["upload_lease_expires_at"] = (
            version.upload_lease_expires_at.replace(tzinfo=None)
        )

        parsed = MongoArtifactRepository._parse_version(document)

        self.assertGreater(parsed.upload_lease_expires_at, now)
        self.assertEqual(parsed.upload_lease_expires_at.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
