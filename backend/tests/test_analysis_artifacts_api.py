from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apis.analysis_artifacts import router
from apis.deps import current_user_id, verify_internal_secret
from scripts.data_analysis_agent.runtime.models import (
    ArtifactSource,
    ArtifactVersion,
    ArtifactVersionStatus,
    BlobReference,
    WorkspaceArtifact,
    WorkspaceArtifactType,
)
from scripts.data_analysis_agent.runtime.services.artifacts import (
    ArtifactUploadResult,
)


_NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


class _ArtifactService:
    def __init__(self) -> None:
        self.create_requests: list[object] = []
        self.download_requests: list[dict[str, object]] = []

    async def create_version(self, request: object) -> ArtifactUploadResult:
        self.create_requests.append(request)
        artifact_id = str(getattr(request, "artifact_id"))
        workspace_id = str(getattr(request, "workspace_id"))
        user_id = str(getattr(request, "user_id"))
        version_id = str(getattr(request, "version_id"))
        artifact_type = getattr(request, "artifact_type")
        content = bytes(getattr(request, "content"))
        blob = BlobReference(
            provider="cloudinary",
            object_key=f"docmind/{version_id}/sales.csv",
            content_type="text/csv",
            filename="sales.csv",
            byte_count=len(content),
            sha256="a" * 64,
            created_at=_NOW,
        )
        version = ArtifactVersion(
            version_id=version_id,
            artifact_id=artifact_id,
            user_id=user_id,
            workspace_id=workspace_id,
            version_number=1,
            content_hash="a" * 64,
            byte_count=len(content),
            content_type="text/csv",
            filename="sales.csv",
            status=ArtifactVersionStatus.READY,
            blob=blob,
            created_at=_NOW,
            updated_at=_NOW,
            ready_at=_NOW,
        )
        artifact = WorkspaceArtifact(
            artifact_id=artifact_id,
            user_id=user_id,
            workspace_id=workspace_id,
            artifact_type=artifact_type,
            name=str(getattr(request, "artifact_name")),
            source=ArtifactSource.UPLOADED,
            current_version_id=version_id,
            created_at=_NOW,
            updated_at=_NOW,
        )
        return ArtifactUploadResult(artifact=artifact, version=version)

    async def signed_download_url(self, **kwargs: object) -> str:
        self.download_requests.append(kwargs)
        return "https://example.invalid/private-download"


def _client(service: _ArtifactService) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.analysis_artifact_service = service
    app.dependency_overrides[current_user_id] = lambda: "user-1"
    app.dependency_overrides[verify_internal_secret] = lambda: None
    return TestClient(app)


class AnalysisArtifactAPITests(unittest.TestCase):
    def test_upload_is_tenant_scoped_and_idempotently_versioned(self) -> None:
        service = _ArtifactService()
        with _client(service) as client:
            response = client.post(
                "/analysis/artifacts",
                headers={"Idempotency-Key": "upload-request-1"},
                data={
                    "workspace_id": "workspace-1",
                    "artifact_id": "sales-data",
                    "artifact_type": "csv",
                    "artifact_name": "Sales data",
                },
                files={"file": ("sales.csv", b"revenue\n50001\n", "text/csv")},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["artifact_id"], "sales-data")
        self.assertNotIn("blob", response.json())
        request = service.create_requests[0]
        self.assertEqual(getattr(request, "user_id"), "user-1")
        self.assertEqual(getattr(request, "source"), ArtifactSource.UPLOADED)
        self.assertEqual(len(str(getattr(request, "version_id"))), 36)

    def test_upload_rejects_non_analysis_artifact_type(self) -> None:
        service = _ArtifactService()
        with _client(service) as client:
            response = client.post(
                "/analysis/artifacts",
                headers={"Idempotency-Key": "upload-request-2"},
                data={
                    "workspace_id": "workspace-1",
                    "artifact_id": "document",
                    "artifact_type": "pdf",
                    "artifact_name": "Document",
                },
                files={"file": ("document.csv", b"a\n1\n", "text/csv")},
            )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(service.create_requests)

    def test_snapshot_upload_persists_complete_provenance_metadata(self) -> None:
        service = _ArtifactService()
        with _client(service) as client:
            response = client.post(
                "/analysis/artifacts",
                headers={"Idempotency-Key": "snapshot-upload-1"},
                data={
                    "workspace_id": "workspace-1",
                    "artifact_id": "workbook-1",
                    "artifact_type": "spreadsheet",
                    "artifact_name": "Revenue workbook",
                    "workbook_id": "workbook-1",
                    "worksheet_id": "sheet-1",
                    "snapshot_range": "Sheet1!A1:B2",
                    "snapshot_hash": "b" * 64,
                    "client_revision": "7",
                },
                files={
                    "file": (
                        "workbook-snapshot.json",
                        b'{"range_a1":"Sheet1!A1:B2"}',
                        "application/json",
                    )
                },
            )

        self.assertEqual(response.status_code, 201)
        request = service.create_requests[0]
        self.assertEqual(
            getattr(request, "metadata"),
            {
                "workbook_id": "workbook-1",
                "worksheet_id": "sheet-1",
                "range": "Sheet1!A1:B2",
                "snapshot_hash": "b" * 64,
                "client_revision": 7,
            },
        )

    def test_snapshot_upload_rejects_partial_metadata(self) -> None:
        service = _ArtifactService()
        with _client(service) as client:
            response = client.post(
                "/analysis/artifacts",
                headers={"Idempotency-Key": "snapshot-upload-2"},
                data={
                    "workspace_id": "workspace-1",
                    "artifact_id": "workbook-1",
                    "artifact_type": "spreadsheet",
                    "artifact_name": "Revenue workbook",
                    "workbook_id": "workbook-1",
                },
                files={
                    "file": (
                        "workbook-snapshot.json",
                        b"{}",
                        "application/json",
                    )
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(service.create_requests)

    def test_signed_download_forwards_authenticated_tenant(self) -> None:
        service = _ArtifactService()
        with _client(service) as client:
            response = client.get(
                "/analysis/artifacts/versions/version-1/download-url",
                params={
                    "workspace_id": "workspace-1",
                    "expires_in_seconds": 300,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["expires_in_seconds"], 300)
        self.assertEqual(
            response.headers["cache-control"],
            "no-store, private",
        )
        self.assertEqual(
            service.download_requests[0]["user_id"],
            "user-1",
        )


if __name__ == "__main__":
    unittest.main()
