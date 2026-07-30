from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import threading
import unittest
import zipfile
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

from cloudinary.exceptions import AlreadyExists

from scripts.data_analysis_agent.runtime.models import (
    ActiveArtifactContext,
    DatasetCatalogEntry,
    DatasetColumnType,
    SpreadsheetContext,
    TabularDataset,
    WorkbookCellType,
    WorkbookRangeSnapshot,
    canonical_snapshot_hash,
)
from scripts.data_analysis_agent.runtime.models.artifacts import (
    ArtifactSource,
    ArtifactVersion,
    ArtifactVersionStatus,
    BlobReference,
    WorkspaceArtifact,
    WorkspaceArtifactType,
)
from scripts.data_analysis_agent.runtime.repositories.artifacts import (
    ArtifactRepositoryError,
    ArtifactStateConflictError,
    ArtifactUploadLeaseConflictError,
    ArtifactVersionDraft,
    ArtifactVersionReservation,
    MongoArtifactRepository,
)
from scripts.data_analysis_agent.runtime.services.artifacts import (
    ArtifactFinalizationPendingError,
    ArtifactUploadFailedError,
    ArtifactVersionService,
    CreateArtifactVersion,
)
from scripts.data_analysis_agent.runtime.services.workbook_context import (
    WorkbookContextError,
    WorkbookContextLimits,
    WorkbookContextService,
    WorkbookContextTooLargeError,
)
from scripts.data_analysis_agent.runtime.storage.base import (
    BlobIntegrityError,
    BlobNotFoundError,
    BlobStat,
    BlobStoreError,
    BlobStoreUnavailableError,
    BlobUpload,
)
from scripts.data_analysis_agent.runtime.storage.cloudinary import (
    CloudinaryArtifactBlobStore,
    CloudinaryBlobStoreConfig,
)
from scripts.data_analysis_agent.runtime.storage.validation import (
    ArtifactValidationError,
    ArtifactValidationLimits,
    ArtifactValidationProfile,
    validate_artifact,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _xlsx_bytes(
    *,
    extra_members: dict[str, bytes] | None = None,
    content_types: bytes | None = None,
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            content_types
            or b'<?xml version="1.0"?><Types xmlns="urn:test"></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            b'<?xml version="1.0"?><Relationships xmlns="urn:test"></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            b'<?xml version="1.0"?><workbook xmlns="urn:test"></workbook>',
        )
        for name, content in (extra_members or {}).items():
            archive.writestr(name, content)
    return output.getvalue()


class InMemoryArtifactRepository:
    def __init__(self) -> None:
        self.artifacts: dict[tuple[str, str, str], WorkspaceArtifact] = {}
        self.versions: dict[tuple[str, str, str], ArtifactVersion] = {}
        self.version_counters: dict[tuple[str, str, str], int] = {}
        self.fail_finalize_once = False
        self.fail_record_blob_once = False

    async def ensure_artifact(
        self,
        artifact: WorkspaceArtifact,
    ) -> WorkspaceArtifact:
        key = (artifact.user_id, artifact.workspace_id, artifact.artifact_id)
        existing = self.artifacts.get(key)
        if existing is not None:
            if existing.artifact_type != artifact.artifact_type:
                raise ArtifactStateConflictError("artifact identity conflict")
            return existing
        self.artifacts[key] = artifact
        self.version_counters[key] = 0
        return artifact

    async def reserve_version(
        self,
        draft: ArtifactVersionDraft,
    ) -> ArtifactVersionReservation:
        version_key = (draft.user_id, draft.workspace_id, draft.version_id)
        existing = self.versions.get(version_key)
        if existing is not None:
            MongoArtifactRepository._assert_same_draft(existing, draft)
            return ArtifactVersionReservation(version=existing, created=False)
        artifact_key = (draft.user_id, draft.workspace_id, draft.artifact_id)
        artifact = self.artifacts.get(artifact_key)
        if artifact is None:
            raise ArtifactRepositoryError("artifact missing")
        number = self.version_counters[artifact_key] + 1
        self.version_counters[artifact_key] = number
        version = ArtifactVersion(
            version_id=draft.version_id,
            artifact_id=draft.artifact_id,
            user_id=draft.user_id,
            workspace_id=draft.workspace_id,
            version_number=number,
            content_hash=draft.content_hash,
            byte_count=draft.byte_count,
            content_type=draft.content_type,
            filename=draft.filename,
            status=ArtifactVersionStatus.UPLOADING,
            parent_version_id=draft.parent_version_id or artifact.current_version_id,
            parent_version_is_explicit=draft.parent_version_id is not None,
            metadata=dict(draft.metadata or {}),
        )
        self.versions[version_key] = version
        return ArtifactVersionReservation(version=version, created=True)

    async def get_artifact(
        self,
        *,
        user_id: str,
        workspace_id: str,
        artifact_id: str,
    ) -> WorkspaceArtifact | None:
        return self.artifacts.get((user_id, workspace_id, artifact_id))

    async def get_version(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
    ) -> ArtifactVersion | None:
        return self.versions.get((user_id, workspace_id, version_id))

    async def claim_upload_lease(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        upload_owner_id: str,
        current_time: datetime,
        lease_expires_at: datetime,
    ) -> ArtifactVersion:
        key = (user_id, workspace_id, version_id)
        version = self.versions[key]
        live_owner = (
            version.upload_owner_id is not None
            and version.upload_lease_expires_at is not None
            and version.upload_lease_expires_at > current_time
        )
        if (
            version.status != ArtifactVersionStatus.UPLOADING
            or (live_owner and version.upload_owner_id != upload_owner_id)
        ):
            raise ArtifactUploadLeaseConflictError("upload lease unavailable")
        if live_owner:
            return version
        claimed = ArtifactVersion.model_validate(
            version.model_copy(
                update={
                    "upload_owner_id": upload_owner_id,
                    "upload_lease_expires_at": lease_expires_at,
                    "upload_attempt": version.upload_attempt + 1,
                    "updated_at": current_time,
                }
            ).model_dump()
        )
        self.versions[key] = claimed
        return claimed

    async def renew_upload_lease(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        upload_owner_id: str,
        current_time: datetime,
        lease_expires_at: datetime,
    ) -> ArtifactVersion:
        key = (user_id, workspace_id, version_id)
        version = self.versions[key]
        if (
            version.status != ArtifactVersionStatus.UPLOADING
            or version.upload_owner_id != upload_owner_id
            or version.upload_lease_expires_at is None
            or version.upload_lease_expires_at <= current_time
        ):
            raise ArtifactUploadLeaseConflictError("upload lease lost")
        renewed = ArtifactVersion.model_validate(
            version.model_copy(
                update={
                    "upload_lease_expires_at": lease_expires_at,
                    "updated_at": current_time,
                }
            ).model_dump()
        )
        self.versions[key] = renewed
        return renewed

    async def release_upload_lease(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        upload_owner_id: str,
        current_time: datetime,
    ) -> ArtifactVersion:
        key = (user_id, workspace_id, version_id)
        version = self.versions[key]
        if version.status != ArtifactVersionStatus.UPLOADING:
            return version
        if version.upload_owner_id != upload_owner_id:
            raise ArtifactUploadLeaseConflictError("upload lease lost")
        released = ArtifactVersion.model_validate(
            version.model_copy(
                update={
                    "upload_owner_id": None,
                    "upload_lease_expires_at": None,
                    "updated_at": current_time,
                }
            ).model_dump()
        )
        self.versions[key] = released
        return released

    async def mark_version_ready(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        blob: BlobReference,
        upload_owner_id: str | None = None,
    ) -> ArtifactVersion:
        key = (user_id, workspace_id, version_id)
        version = self.versions[key]
        if version.status == ArtifactVersionStatus.UPLOADING:
            if upload_owner_id is not None and (
                version.upload_owner_id != upload_owner_id
                or version.upload_lease_expires_at is None
                or version.upload_lease_expires_at <= _utc_now()
            ):
                raise ArtifactUploadLeaseConflictError("upload lease lost")
            now = _utc_now()
            version = version.model_copy(
                update={
                    "status": ArtifactVersionStatus.READY,
                    "blob": blob,
                    "upload_owner_id": None,
                    "upload_lease_expires_at": None,
                    "ready_at": now,
                    "updated_at": now,
                }
            )
            # model_copy is deliberately fast and does not revalidate.
            version = ArtifactVersion.model_validate(version.model_dump())
            self.versions[key] = version
        elif version.status != ArtifactVersionStatus.READY or version.blob != blob:
            raise ArtifactStateConflictError("invalid ready transition")

        artifact_key = (user_id, workspace_id, version.artifact_id)
        artifact = self.artifacts[artifact_key]
        current = (
            await self._current_version_number(artifact)
            if artifact.current_version_id
            else 0
        )
        if version.version_number >= current:
            now = _utc_now()
            self.artifacts[artifact_key] = WorkspaceArtifact.model_validate(
                artifact.model_copy(
                    update={
                        "current_version_id": version.version_id,
                        "updated_at": now,
                    }
                ).model_dump()
            )
        if self.fail_finalize_once:
            self.fail_finalize_once = False
            raise ArtifactRepositoryError("simulated lost acknowledgement")
        return version

    async def record_uploaded_blob(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        blob: BlobReference,
        upload_owner_id: str | None = None,
    ) -> ArtifactVersion:
        if self.fail_record_blob_once:
            self.fail_record_blob_once = False
            raise ArtifactRepositoryError("simulated record acknowledgement loss")
        key = (user_id, workspace_id, version_id)
        version = self.versions[key]
        if version.status != ArtifactVersionStatus.UPLOADING:
            raise ArtifactStateConflictError("invalid blob transition")
        if upload_owner_id is not None and (
            version.upload_owner_id != upload_owner_id
            or version.upload_lease_expires_at is None
            or version.upload_lease_expires_at <= _utc_now()
        ):
            raise ArtifactUploadLeaseConflictError("upload lease lost")
        if version.blob is not None and version.blob != blob:
            raise ArtifactStateConflictError("different blob already recorded")
        if version.blob is None:
            version = ArtifactVersion.model_validate(
                version.model_copy(
                    update={"blob": blob, "updated_at": _utc_now()}
                ).model_dump()
            )
            self.versions[key] = version
        return version

    async def mark_version_failed(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        error_code: str,
        error_message: str,
        upload_owner_id: str | None = None,
    ) -> ArtifactVersion:
        key = (user_id, workspace_id, version_id)
        version = self.versions[key]
        if version.status != ArtifactVersionStatus.UPLOADING:
            return version
        if upload_owner_id is not None and (
            version.upload_owner_id != upload_owner_id
            or version.upload_lease_expires_at is None
            or version.upload_lease_expires_at <= _utc_now()
        ):
            raise ArtifactUploadLeaseConflictError("upload lease lost")
        now = _utc_now()
        failed = ArtifactVersion.model_validate(
            version.model_copy(
                update={
                    "status": ArtifactVersionStatus.FAILED,
                    "error_code": error_code,
                    "error_message": error_message,
                    "blob": None,
                    "upload_owner_id": None,
                    "upload_lease_expires_at": None,
                    "failed_at": now,
                    "updated_at": now,
                }
            ).model_dump()
        )
        self.versions[key] = failed
        return failed

    async def _current_version_number(self, artifact: WorkspaceArtifact) -> int:
        if artifact.current_version_id is None:
            return 0
        current = self.versions[
            (artifact.user_id, artifact.workspace_id, artifact.current_version_id)
        ]
        return current.version_number


class InMemoryBlobStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.references: dict[str, BlobReference] = {}
        self.upload_calls = 0
        self.verify_calls = 0
        self.deleted: list[str] = []
        self.fail_upload = False
        self.accept_then_timeout_once = False
        self.raw_timeout_once = False
        self.fail_verify = False
        self.fail_verify_unavailable_once = False

    async def upload(self, upload: BlobUpload) -> BlobReference:
        self.upload_calls += 1
        if self.fail_upload:
            raise BlobStoreError("simulated definitive provider rejection")
        if self.raw_timeout_once:
            self.raw_timeout_once = False
            raise TimeoutError("simulated raw provider timeout")
        if upload.object_key in self.objects:
            return self.references[upload.object_key]
        reference = BlobReference(
            provider="cloudinary",
            object_key=upload.object_key,
            provider_asset_id=f"asset-{self.upload_calls}",
            provider_version=str(self.upload_calls),
            resource_type="raw",
            delivery_type="authenticated",
            content_type=upload.content_type,
            filename=upload.filename,
            byte_count=upload.byte_count,
            sha256=upload.sha256,
        )
        self.objects[upload.object_key] = upload.content
        self.references[upload.object_key] = reference
        if self.accept_then_timeout_once:
            self.accept_then_timeout_once = False
            raise BlobStoreUnavailableError(
                "simulated timeout after provider acceptance"
            )
        return reference

    async def stat(self, reference: BlobReference) -> BlobStat:
        content = self.objects.get(reference.object_key)
        if content is None:
            raise BlobNotFoundError("missing")
        return BlobStat(
            object_key=reference.object_key,
            byte_count=len(content),
            provider_version=reference.provider_version,
            provider_asset_id=reference.provider_asset_id,
            stored_sha256=hashlib.sha256(content).hexdigest(),
        )

    async def download(
        self,
        reference: BlobReference,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        content = self.objects.get(reference.object_key)
        if content is None:
            raise BlobNotFoundError("missing")
        if max_bytes is not None and len(content) > max_bytes:
            raise BlobIntegrityError("too large")
        return content

    async def signed_download_url(
        self,
        reference: BlobReference,
        *,
        expires_in_seconds: int = 900,
    ) -> str:
        del expires_in_seconds
        if reference.object_key not in self.objects:
            raise BlobNotFoundError("missing")
        return f"https://storage.test/{reference.object_key}?signed=1"

    async def generate_signed_download(
        self,
        reference: BlobReference,
        *,
        expires_in_seconds: int = 900,
    ) -> str:
        return await self.signed_download_url(
            reference,
            expires_in_seconds=expires_in_seconds,
        )

    async def verify_checksum(self, reference: BlobReference) -> None:
        self.verify_calls += 1
        if self.fail_verify_unavailable_once:
            self.fail_verify_unavailable_once = False
            raise BlobStoreUnavailableError("simulated verification outage")
        if self.fail_verify:
            raise BlobIntegrityError("simulated corruption")
        content = await self.download(reference)
        if (
            len(content) != reference.byte_count
            or hashlib.sha256(content).hexdigest() != reference.sha256
        ):
            raise BlobIntegrityError("mismatch")

    async def delete(self, reference: BlobReference) -> bool:
        self.deleted.append(reference.object_key)
        existed = self.objects.pop(reference.object_key, None) is not None
        self.references.pop(reference.object_key, None)
        return existed


class InMemoryDatasetCatalog:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], DatasetCatalogEntry] = {}

    async def register(self, entry: DatasetCatalogEntry) -> DatasetCatalogEntry:
        identity = (entry.handle.dataset_id, entry.handle.source_version)
        existing = self.entries.get(identity)
        if existing is not None:
            return existing
        self.entries[identity] = entry
        return entry


class ExistingArtifactCollection:
    def __init__(self, artifact: WorkspaceArtifact) -> None:
        self.document = artifact.model_dump(mode="python")
        self.last_query: dict[str, str] | None = None

    async def update_one(
        self,
        query: dict[str, str],
        update: object,
        *,
        upsert: bool,
    ) -> None:
        del update, upsert
        self.last_query = query

    async def find_one(
        self,
        query: dict[str, str],
        projection: object,
    ) -> dict[str, object] | None:
        del projection
        return self.document if query == self.last_query else None


class ArtifactRepositoryPolicyTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _version(
        *,
        parent_version_id: str | None = "version-0",
        parent_version_is_explicit: bool | None = True,
        metadata: dict[str, object] | None = None,
    ) -> ArtifactVersion:
        return ArtifactVersion(
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
            parent_version_id=parent_version_id,
            parent_version_is_explicit=parent_version_is_explicit,
            metadata=metadata or {},
        )

    @staticmethod
    def _draft(
        *,
        parent_version_id: str | None = "version-0",
        metadata: dict[str, object] | None = None,
    ) -> ArtifactVersionDraft:
        return ArtifactVersionDraft(
            version_id="version-1",
            artifact_id="artifact-1",
            user_id="user-1",
            workspace_id="workspace-1",
            content_hash="a" * 64,
            byte_count=10,
            content_type="application/json",
            filename="snapshot.json",
            parent_version_id=parent_version_id,
            metadata=metadata or {},
        )

    def test_replay_accepts_canonically_equal_metadata(self) -> None:
        version = self._version(
            metadata={
                "client_revision": 7,
                "source": {
                    "worksheet": "Sheet1",
                    "columns": ["revenue", "region"],
                },
            }
        )
        draft = self._draft(
            metadata={
                "source": {
                    "columns": ["revenue", "region"],
                    "worksheet": "Sheet1",
                },
                "client_revision": 7,
            }
        )

        MongoArtifactRepository._assert_same_draft(version, draft)

    def test_replay_rejects_changed_metadata(self) -> None:
        version = self._version(metadata={"client_revision": 7})
        draft = self._draft(metadata={"client_revision": 8})

        with self.assertRaisesRegex(
            ArtifactStateConflictError,
            "content or provenance",
        ):
            MongoArtifactRepository._assert_same_draft(version, draft)

    def test_replay_rejects_changed_parent_intent(self) -> None:
        version = self._version()

        for parent_version_id in (None, "version-other"):
            with self.subTest(parent_version_id=parent_version_id):
                with self.assertRaises(ArtifactStateConflictError):
                    MongoArtifactRepository._assert_same_draft(
                        version,
                        self._draft(parent_version_id=parent_version_id),
                    )

    def test_implicit_parent_replay_matches_its_resolved_parent(self) -> None:
        version = self._version(parent_version_is_explicit=False)

        MongoArtifactRepository._assert_same_draft(
            version,
            self._draft(parent_version_id=None),
        )

    async def test_mongo_identity_keeps_source_and_tenant_scope(self) -> None:
        existing = WorkspaceArtifact(
            artifact_id="artifact-1",
            user_id="user-1",
            workspace_id="workspace-1",
            artifact_type=WorkspaceArtifactType.SPREADSHEET,
            name="Workbook",
            source=ArtifactSource.UPLOADED,
        )
        collection = ExistingArtifactCollection(existing)
        database = {MongoArtifactRepository.artifacts_collection_name: collection}
        candidate = existing.model_copy(update={"source": ArtifactSource.CREATED})

        with patch(
            "scripts.data_analysis_agent.runtime.repositories.artifacts.get_db",
            return_value=database,
        ):
            resolved = await MongoArtifactRepository().ensure_artifact(candidate)

        self.assertEqual(resolved.source, ArtifactSource.UPLOADED)
        self.assertEqual(
            collection.last_query,
            {
                "user_id": "user-1",
                "workspace_id": "workspace-1",
                "artifact_id": "artifact-1",
            },
        )

    async def test_mongo_identity_still_rejects_type_change(self) -> None:
        existing = WorkspaceArtifact(
            artifact_id="artifact-1",
            user_id="user-1",
            workspace_id="workspace-1",
            artifact_type=WorkspaceArtifactType.SPREADSHEET,
            name="Workbook",
            source=ArtifactSource.UPLOADED,
        )
        collection = ExistingArtifactCollection(existing)
        database = {MongoArtifactRepository.artifacts_collection_name: collection}
        candidate = existing.model_copy(
            update={"artifact_type": WorkspaceArtifactType.CSV}
        )

        with (
            patch(
                "scripts.data_analysis_agent.runtime.repositories.artifacts.get_db",
                return_value=database,
            ),
            self.assertRaises(ArtifactStateConflictError),
        ):
            await MongoArtifactRepository().ensure_artifact(candidate)


class ArtifactValidationTests(unittest.TestCase):
    def test_csv_and_json_are_validated_and_hashed(self) -> None:
        csv_content = b"region,revenue\nAPAC,51000\n"
        csv_result = validate_artifact(
            csv_content,
            filename="../../sales.csv",
            content_type="text/csv; charset=utf-8",
        )
        self.assertEqual(csv_result.filename, "sales.csv")
        self.assertEqual(csv_result.kind, "csv")
        self.assertEqual(
            csv_result.sha256,
            hashlib.sha256(csv_content).hexdigest(),
        )

        json_result = validate_artifact(
            json.dumps({"sheet": "Sheet1", "values": [[1, 2]]}).encode(),
            filename="snapshot.json",
            content_type="application/json",
        )
        self.assertEqual(json_result.kind, "json")

        compressed = gzip.compress(b'{"snapshot":[[1,2],[3,4]]}')
        compressed_result = validate_artifact(
            compressed,
            filename="workbook-snapshot.json.gz",
            content_type="application/gzip",
        )
        self.assertEqual(compressed_result.kind, "json")
        self.assertTrue(compressed_result.is_compressed)

    def test_only_trusted_server_gzip_bypasses_compression_ratio(self) -> None:
        size = 100
        snapshot = WorkbookRangeSnapshot(
            range_a1="A1:CV100",
            values=((0,) * size,) * size,
            formulas=((None,) * size,) * size,
            cell_types=((WorkbookCellType.NUMBER,) * size,) * size,
            number_formats=((None,) * size,) * size,
            row_count=size,
            column_count=size,
        )
        compressed = gzip.compress(
            snapshot.model_dump_json().encode("utf-8"),
            mtime=0,
        )
        self.assertGreater(
            len(gzip.decompress(compressed)) / len(compressed),
            ArtifactValidationLimits().max_compression_ratio,
        )

        with self.assertRaises(ArtifactValidationError) as untrusted:
            validate_artifact(
                compressed,
                filename="snapshot.json.gz",
                content_type="application/gzip",
            )
        self.assertEqual(untrusted.exception.code, "archive_ratio_limit")

        trusted = validate_artifact(
            compressed,
            filename="snapshot.json.gz",
            content_type="application/gzip",
            profile=ArtifactValidationProfile.TRUSTED_SERVER_GENERATED,
        )
        self.assertEqual(trusted.kind, "json")
        self.assertTrue(trusted.is_compressed)

        with self.assertRaises(ArtifactValidationError) as oversized:
            validate_artifact(
                compressed,
                filename="snapshot.json.gz",
                content_type="application/gzip",
                limits=ArtifactValidationLimits(
                    max_archive_uncompressed_bytes=1_000
                ),
                profile=ArtifactValidationProfile.TRUSTED_SERVER_GENERATED,
            )
        self.assertEqual(oversized.exception.code, "archive_size_limit")

    def test_contradictory_content_type_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ArtifactValidationError,
            "does not match",
        ) as captured:
            validate_artifact(
                b"a,b\n1,2\n",
                filename="data.csv",
                content_type="image/png",
            )
        self.assertEqual(captured.exception.code, "content_type_mismatch")

    def test_xlsm_and_macro_payloads_are_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError) as xlsm:
            validate_artifact(b"not-used", filename="unsafe.xlsm")
        self.assertEqual(xlsm.exception.code, "macro_workbook_unsupported")

        workbook = _xlsx_bytes(
            extra_members={"xl/vbaProject.bin": b"macro bytes"}
        )
        with self.assertRaises(ArtifactValidationError) as macro:
            validate_artifact(workbook, filename="unsafe.xlsx")
        self.assertEqual(macro.exception.code, "active_content_unsupported")

    def test_minimal_xlsx_is_accepted(self) -> None:
        workbook = _xlsx_bytes()
        result = validate_artifact(
            workbook,
            filename="workbook.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
        self.assertEqual(result.kind, "xlsx")

    def test_zip_bomb_ratio_is_rejected_before_extraction(self) -> None:
        workbook = _xlsx_bytes(
            extra_members={"xl/worksheets/sheet1.xml": b"A" * 200_000}
        )
        with self.assertRaises(ArtifactValidationError) as captured:
            validate_artifact(
                workbook,
                filename="bomb.xlsx",
                limits=ArtifactValidationLimits(max_compression_ratio=10),
            )
        self.assertEqual(captured.exception.code, "archive_ratio_limit")

    def test_xlsx_member_path_traversal_is_rejected(self) -> None:
        workbook = _xlsx_bytes(extra_members={"../outside.xml": b"unsafe"})
        with self.assertRaises(ArtifactValidationError) as captured:
            validate_artifact(workbook, filename="unsafe.xlsx")
        self.assertEqual(captured.exception.code, "unsafe_archive_path")


class ArtifactVersionServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = InMemoryArtifactRepository()
        self.store = InMemoryBlobStore()
        self.service = ArtifactVersionService(
            repository=self.repository,
            blob_store=self.store,
        )
        self.content = b"region,revenue\nAPAC,51000\nEMEA,49000\n"

    def request(self, **overrides: object) -> CreateArtifactVersion:
        values: dict[str, object] = {
            "user_id": "user-1",
            "workspace_id": "workspace-1",
            "artifact_id": "artifact-1",
            "artifact_type": WorkspaceArtifactType.SPREADSHEET,
            "artifact_name": "Revenue workbook",
            "source": ArtifactSource.CREATED,
            "filename": "Sheet1-A1-B3.csv",
            "content_type": "text/csv",
            "content": self.content,
            "version_id": "version-1",
        }
        values.update(overrides)
        return CreateArtifactVersion(**values)  # type: ignore[arg-type]

    async def test_two_stage_upload_finishes_ready_and_promotes(self) -> None:
        result = await self.service.create_version(self.request())

        self.assertEqual(result.version.status, ArtifactVersionStatus.READY)
        self.assertEqual(result.artifact.current_version_id, "version-1")
        self.assertEqual(self.store.upload_calls, 1)
        self.assertEqual(self.store.verify_calls, 1)
        assert result.version.blob is not None
        self.assertEqual(result.version.blob.delivery_type.value, "authenticated")
        self.assertEqual(result.version.blob.resource_type.value, "raw")
        self.assertIn(
            "docmind/user-1/workspace-1/artifact-1/version-1/",
            result.version.blob.object_key,
        )

    async def test_ready_retry_is_idempotent_without_second_upload(self) -> None:
        first = await self.service.create_version(self.request())
        second = await self.service.create_version(self.request())

        self.assertEqual(first.version, second.version)
        self.assertEqual(self.store.upload_calls, 1)

    async def test_retry_rejects_changed_version_metadata(self) -> None:
        await self.service.create_version(
            self.request(metadata={"client_revision": 7})
        )

        with self.assertRaises(ArtifactStateConflictError):
            await self.service.create_version(
                self.request(metadata={"client_revision": 8})
            )

        self.assertEqual(self.store.upload_calls, 1)

    async def test_ready_retry_repairs_stale_current_version_pointer(self) -> None:
        first = await self.service.create_version(self.request())
        key = ("user-1", "workspace-1", "artifact-1")
        stale = self.repository.artifacts[key]
        self.repository.artifacts[key] = WorkspaceArtifact.model_validate(
            stale.model_copy(
                update={
                    "current_version_id": None,
                    "updated_at": _utc_now(),
                }
            ).model_dump()
        )

        replayed = await self.service.create_version(self.request())

        self.assertEqual(replayed.version, first.version)
        self.assertEqual(replayed.artifact.current_version_id, "version-1")
        self.assertEqual(self.store.upload_calls, 1)

    async def test_existing_artifact_preserves_first_source_across_versions(
        self,
    ) -> None:
        first = await self.service.create_version(
            self.request(source=ArtifactSource.UPLOADED)
        )
        second = await self.service.create_version(
            self.request(
                source=ArtifactSource.CREATED,
                version_id="version-2",
            )
        )

        self.assertEqual(first.artifact.source, ArtifactSource.UPLOADED)
        self.assertEqual(second.artifact.source, ArtifactSource.UPLOADED)
        self.assertEqual(second.artifact.current_version_id, "version-2")
        self.assertEqual(first.version.metadata["version_source"], "uploaded")
        self.assertEqual(second.version.metadata["version_source"], "created")

    async def test_existing_artifact_type_remains_an_identity_invariant(
        self,
    ) -> None:
        await self.service.create_version(
            self.request(source=ArtifactSource.UPLOADED)
        )

        with self.assertRaises(ArtifactStateConflictError):
            await self.service.create_version(
                self.request(
                    artifact_type=WorkspaceArtifactType.CSV,
                    source=ArtifactSource.CREATED,
                    version_id="version-2",
                )
            )

    async def test_provider_failure_marks_version_failed(self) -> None:
        self.store.fail_upload = True
        with self.assertRaises(ArtifactUploadFailedError) as captured:
            await self.service.create_version(self.request())

        self.assertEqual(captured.exception.code, "artifact_storage_failed")
        version = await self.repository.get_version(
            user_id="user-1",
            workspace_id="workspace-1",
            version_id="version-1",
        )
        assert version is not None
        self.assertEqual(version.status, ArtifactVersionStatus.FAILED)
        self.assertIsNone(version.blob)

    async def test_ambiguous_upload_timeout_remains_resumable(self) -> None:
        self.store.accept_then_timeout_once = True
        with self.assertRaises(ArtifactFinalizationPendingError):
            await self.service.create_version(self.request())

        pending = await self.repository.get_version(
            user_id="user-1",
            workspace_id="workspace-1",
            version_id="version-1",
        )
        assert pending is not None
        self.assertEqual(pending.status, ArtifactVersionStatus.UPLOADING)
        self.assertIsNone(pending.blob)
        self.assertEqual(len(self.store.objects), 1)

        recovered = await self.service.create_version(self.request())

        self.assertEqual(recovered.version.status, ArtifactVersionStatus.READY)
        self.assertEqual(recovered.artifact.current_version_id, "version-1")
        self.assertEqual(self.store.upload_calls, 2)
        self.assertEqual(len(self.store.objects), 1)

    async def test_raw_upload_timeout_also_remains_resumable(self) -> None:
        self.store.raw_timeout_once = True
        with self.assertRaises(ArtifactFinalizationPendingError):
            await self.service.create_version(self.request())

        pending = await self.repository.get_version(
            user_id="user-1",
            workspace_id="workspace-1",
            version_id="version-1",
        )
        assert pending is not None
        self.assertEqual(pending.status, ArtifactVersionStatus.UPLOADING)
        self.assertIsNone(pending.error_code)

        recovered = await self.service.create_version(self.request())
        self.assertEqual(recovered.version.status, ArtifactVersionStatus.READY)

    async def test_checksum_failure_deletes_blob_and_marks_failed(self) -> None:
        self.store.fail_verify = True
        with self.assertRaises(ArtifactUploadFailedError) as captured:
            await self.service.create_version(self.request())

        self.assertEqual(captured.exception.code, "artifact_integrity_failed")
        self.assertEqual(len(self.store.deleted), 1)
        version = await self.repository.get_version(
            user_id="user-1",
            workspace_id="workspace-1",
            version_id="version-1",
        )
        assert version is not None
        self.assertEqual(version.status, ArtifactVersionStatus.FAILED)

    async def test_lost_finalize_acknowledgement_is_retried_safely(self) -> None:
        self.repository.fail_finalize_once = True
        result = await self.service.create_version(self.request())

        self.assertEqual(result.version.status, ArtifactVersionStatus.READY)
        self.assertEqual(result.artifact.current_version_id, "version-1")
        self.assertEqual(self.store.upload_calls, 1)

    async def test_transient_verification_outage_resumes_without_reupload(self) -> None:
        self.store.fail_verify_unavailable_once = True
        with self.assertRaises(ArtifactFinalizationPendingError):
            await self.service.create_version(self.request())

        pending = await self.repository.get_version(
            user_id="user-1",
            workspace_id="workspace-1",
            version_id="version-1",
        )
        assert pending is not None
        self.assertEqual(pending.status, ArtifactVersionStatus.UPLOADING)
        self.assertIsNotNone(pending.blob)

        result = await self.service.create_version(self.request())
        self.assertEqual(result.version.status, ArtifactVersionStatus.READY)
        self.assertEqual(self.store.upload_calls, 1)
        self.assertEqual(self.store.verify_calls, 2)

    async def test_retry_recovers_upload_not_yet_recorded_in_mongo(self) -> None:
        self.repository.fail_record_blob_once = True
        with self.assertRaises(ArtifactFinalizationPendingError):
            await self.service.create_version(self.request())

        pending = await self.repository.get_version(
            user_id="user-1",
            workspace_id="workspace-1",
            version_id="version-1",
        )
        assert pending is not None
        self.assertEqual(pending.status, ArtifactVersionStatus.UPLOADING)
        self.assertIsNone(pending.blob)

        result = await self.service.create_version(self.request())

        self.assertEqual(result.version.status, ArtifactVersionStatus.READY)
        self.assertEqual(self.store.upload_calls, 2)
        self.assertEqual(len(self.store.objects), 1)

    async def test_download_is_tenant_scoped_through_repository(self) -> None:
        result = await self.service.create_version(self.request())
        content = await self.service.download(
            user_id="user-1",
            workspace_id="workspace-1",
            version_id=result.version.version_id,
        )
        self.assertEqual(content, self.content)

        with self.assertRaisesRegex(Exception, "not found"):
            await self.service.download(
                user_id="another-user",
                workspace_id="workspace-1",
                version_id=result.version.version_id,
            )


class WorkbookContextArtifactPolicyTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _small_snapshot() -> WorkbookRangeSnapshot:
        return WorkbookRangeSnapshot(
            range_a1="Sheet1!A1:B2",
            values=(("revenue", "region"), (50_001, "west")),
            formulas=((None, None), (None, None)),
            cell_types=(
                (WorkbookCellType.STRING, WorkbookCellType.STRING),
                (WorkbookCellType.NUMBER, WorkbookCellType.STRING),
            ),
            number_formats=((None, None), ("$#,##0", None)),
            column_headers=("revenue", "region"),
            header_row_index=0,
            row_count=2,
            column_count=2,
        )

    @staticmethod
    def _two_table_snapshot() -> WorkbookRangeSnapshot:
        values = (
            ("revenue", "region", None, None, None),
            (50_001, "west", None, None, None),
            (None, None, None, None, None),
            (None, None, None, "cost", "team"),
            (None, None, None, 20_000, "ops"),
        )
        formulas = ((None,) * 5,) * 5
        cell_types = tuple(
            tuple(
                (
                    WorkbookCellType.BLANK
                    if value is None
                    else WorkbookCellType.NUMBER
                    if isinstance(value, int)
                    else WorkbookCellType.STRING
                )
                for value in row
            )
            for row in values
        )
        return WorkbookRangeSnapshot(
            range_a1="Sheet1!A1:E5",
            values=values,
            formulas=formulas,
            cell_types=cell_types,
            number_formats=formulas,
            row_count=5,
            column_count=5,
        )

    async def test_uploaded_workbook_accepts_trusted_snapshot_version(
        self,
    ) -> None:
        repository = InMemoryArtifactRepository()
        store = InMemoryBlobStore()
        artifact_service = ArtifactVersionService(
            repository=repository,
            blob_store=store,
        )
        uploaded = await artifact_service.create_version(
            CreateArtifactVersion(
                user_id="user-1",
                workspace_id="workspace-1",
                artifact_id="artifact-1",
                artifact_type=WorkspaceArtifactType.SPREADSHEET,
                artifact_name="Uploaded workbook",
                source=ArtifactSource.UPLOADED,
                filename="source.csv",
                content_type="text/csv",
                content=b"revenue\n50000\n",
                version_id="uploaded-version",
            )
        )
        size = 100
        snapshot = WorkbookRangeSnapshot(
            range_a1="A1:CV100",
            values=((0,) * size,) * size,
            formulas=((None,) * size,) * size,
            cell_types=((WorkbookCellType.NUMBER,) * size,) * size,
            number_formats=((None,) * size,) * size,
            row_count=size,
            column_count=size,
        )
        context = SpreadsheetContext(
            workbook_id="workbook-1",
            workbook_name="Uploaded workbook",
            client_revision=7,
            worksheet_id="sheet-1",
            worksheet_name="Sheet1",
            used_range="A1:CV100",
            snapshot_range="A1:CV100",
            snapshot_hash=canonical_snapshot_hash(snapshot),
            snapshot=snapshot,
        )
        service = WorkbookContextService(
            artifact_service=artifact_service,
            dataset_catalog=InMemoryDatasetCatalog(),
        )

        resolved = await service.resolve(
            user_id="user-1",
            workspace_id="workspace-1",
            context=context,
            active_artifact=ActiveArtifactContext(
                client_artifact_id="client-artifact-1",
                artifact_id="artifact-1",
                artifact_version_id=uploaded.version.version_id,
                artifact_type="spreadsheet",
                name="Uploaded workbook",
            ),
        )

        stored = await repository.get_artifact(
            user_id="user-1",
            workspace_id="workspace-1",
            artifact_id="artifact-1",
        )
        assert stored is not None
        self.assertEqual(stored.source, ArtifactSource.UPLOADED)
        self.assertEqual(
            stored.current_version_id,
            resolved.workbook_artifact_version_id,
        )
        self.assertEqual(resolved.dataset_handle.row_count, size)
        self.assertEqual(len(resolved.dataset_handle.columns), size)
        snapshot_version = await repository.get_version(
            user_id="user-1",
            workspace_id="workspace-1",
            version_id=resolved.workbook_artifact_version_id,
        )
        assert snapshot_version is not None
        self.assertEqual(
            snapshot_version.metadata["validation_profile"],
            ArtifactValidationProfile.TRUSTED_SERVER_GENERATED.value,
        )

    async def test_preuploaded_snapshot_is_bound_to_context_metadata(self) -> None:
        repository = InMemoryArtifactRepository()
        store = InMemoryBlobStore()
        artifact_service = ArtifactVersionService(
            repository=repository,
            blob_store=store,
        )
        snapshot = self._small_snapshot()
        snapshot_hash = canonical_snapshot_hash(snapshot)
        uploaded = await artifact_service.create_version(
            CreateArtifactVersion(
                user_id="user-1",
                workspace_id="workspace-1",
                artifact_id="workbook-1",
                artifact_type=WorkspaceArtifactType.SPREADSHEET,
                artifact_name="Revenue workbook",
                source=ArtifactSource.UPLOADED,
                filename="workbook-snapshot.json",
                content_type="application/json",
                content=snapshot.model_dump_json().encode(),
                version_id="snapshot-version-1",
                metadata={
                    "workbook_id": "workbook-1",
                    "worksheet_id": "sheet-1",
                    "range": "Sheet1!A1:B2",
                    "snapshot_hash": snapshot_hash,
                    "client_revision": 7,
                },
            )
        )
        context = SpreadsheetContext(
            workbook_id="workbook-1",
            workbook_name="Revenue workbook",
            client_revision=7,
            worksheet_id="sheet-1",
            worksheet_name="Sheet1",
            used_range="Sheet1!A1:B2",
            snapshot_range="Sheet1!A1:B2",
            snapshot_hash=snapshot_hash,
            snapshot_artifact_version_id=uploaded.version.version_id,
        )
        service = WorkbookContextService(
            artifact_service=artifact_service,
            dataset_catalog=InMemoryDatasetCatalog(),
        )

        resolved = await service.resolve(
            user_id="user-1",
            workspace_id="workspace-1",
            context=context,
            active_artifact=ActiveArtifactContext(
                client_artifact_id="workbook-1",
                artifact_id="workbook-1",
                artifact_version_id=uploaded.version.version_id,
                artifact_type="spreadsheet",
                name="Revenue workbook",
            ),
        )

        self.assertEqual(
            resolved.workbook_artifact_version_id,
            uploaded.version.version_id,
        )
        self.assertEqual(resolved.dataset_handle.row_count, 1)

        mismatched = context.model_copy(
            update={"worksheet_id": "sheet-2"}
        )
        with self.assertRaisesRegex(
            WorkbookContextError,
            "metadata does not match",
        ):
            await service.resolve(
                user_id="user-1",
                workspace_id="workspace-1",
                context=mismatched,
                active_artifact=ActiveArtifactContext(
                    client_artifact_id="workbook-1",
                    artifact_id="workbook-1",
                    artifact_version_id=uploaded.version.version_id,
                    artifact_type="spreadsheet",
                    name="Revenue workbook",
                ),
            )

    async def test_preuploaded_snapshot_enforces_its_own_cell_limit(self) -> None:
        repository = InMemoryArtifactRepository()
        store = InMemoryBlobStore()
        artifact_service = ArtifactVersionService(
            repository=repository,
            blob_store=store,
        )
        snapshot = self._small_snapshot()
        snapshot_hash = canonical_snapshot_hash(snapshot)
        uploaded = await artifact_service.create_version(
            CreateArtifactVersion(
                user_id="user-1",
                workspace_id="workspace-1",
                artifact_id="workbook-1",
                artifact_type=WorkspaceArtifactType.SPREADSHEET,
                artifact_name="Revenue workbook",
                source=ArtifactSource.UPLOADED,
                filename="workbook-snapshot.json",
                content_type="application/json",
                content=snapshot.model_dump_json().encode(),
                version_id="snapshot-version-limited",
                metadata={
                    "workbook_id": "workbook-1",
                    "worksheet_id": "sheet-1",
                    "range": "Sheet1!A1:B2",
                    "snapshot_hash": snapshot_hash,
                    "client_revision": 7,
                },
            )
        )
        service = WorkbookContextService(
            artifact_service=artifact_service,
            dataset_catalog=InMemoryDatasetCatalog(),
            limits=WorkbookContextLimits(max_uploaded_cells=3),
        )
        context = SpreadsheetContext(
            workbook_id="workbook-1",
            workbook_name="Revenue workbook",
            client_revision=7,
            worksheet_id="sheet-1",
            worksheet_name="Sheet1",
            used_range="Sheet1!A1:B2",
            snapshot_range="Sheet1!A1:B2",
            snapshot_hash=snapshot_hash,
            snapshot_artifact_version_id=uploaded.version.version_id,
        )

        with self.assertRaisesRegex(
            WorkbookContextTooLargeError,
            "cell limit",
        ):
            await service.resolve(
                user_id="user-1",
                workspace_id="workspace-1",
                context=context,
                active_artifact=ActiveArtifactContext(
                    client_artifact_id="workbook-1",
                    artifact_id="workbook-1",
                    artifact_version_id=uploaded.version.version_id,
                    artifact_type="spreadsheet",
                    name="Revenue workbook",
                ),
            )

    async def test_xlsx_source_gets_linked_live_workbook_artifact(self) -> None:
        repository = InMemoryArtifactRepository()
        store = InMemoryBlobStore()
        artifact_service = ArtifactVersionService(
            repository=repository,
            blob_store=store,
        )
        source = await artifact_service.create_version(
            CreateArtifactVersion(
                user_id="user-1",
                workspace_id="workspace-1",
                artifact_id="source-xlsx",
                artifact_type=WorkspaceArtifactType.XLSX,
                artifact_name="Revenue.xlsx",
                source=ArtifactSource.UPLOADED,
                filename="revenue.xlsx",
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                content=_xlsx_bytes(),
                version_id="source-xlsx-version",
            )
        )
        snapshot = self._small_snapshot()
        context = SpreadsheetContext(
            workbook_id="workbook-1",
            workbook_name="Revenue.xlsx",
            client_revision=7,
            worksheet_id="sheet-1",
            worksheet_name="Sheet1",
            used_range="Sheet1!A1:B2",
            snapshot_range="Sheet1!A1:B2",
            snapshot_hash=canonical_snapshot_hash(snapshot),
            snapshot=snapshot,
        )
        service = WorkbookContextService(
            artifact_service=artifact_service,
            dataset_catalog=InMemoryDatasetCatalog(),
        )

        resolved = await service.resolve(
            user_id="user-1",
            workspace_id="workspace-1",
            context=context,
            active_artifact=ActiveArtifactContext(
                client_artifact_id="source-xlsx",
                artifact_id="source-xlsx",
                artifact_version_id=source.version.version_id,
                artifact_type="xlsx",
                name="Revenue.xlsx",
            ),
        )

        workbook_artifact_id = resolved.dataset_handle.locator.artifact_id
        self.assertNotEqual(workbook_artifact_id, "source-xlsx")
        self.assertEqual(
            resolved.source_artifact_version_id,
            source.version.version_id,
        )
        original = await repository.get_artifact(
            user_id="user-1",
            workspace_id="workspace-1",
            artifact_id="source-xlsx",
        )
        linked = await repository.get_artifact(
            user_id="user-1",
            workspace_id="workspace-1",
            artifact_id=workbook_artifact_id,
        )
        assert original is not None
        assert linked is not None
        self.assertEqual(original.artifact_type, WorkspaceArtifactType.XLSX)
        self.assertEqual(linked.artifact_type, WorkspaceArtifactType.SPREADSHEET)

        with self.assertRaisesRegex(
            WorkbookContextError,
            "artifact and version do not match",
        ):
            await service.resolve(
                user_id="user-1",
                workspace_id="workspace-1",
                context=context,
                active_artifact=ActiveArtifactContext(
                    client_artifact_id="forged-source",
                    artifact_id="forged-source",
                    artifact_version_id=source.version.version_id,
                    artifact_type="xlsx",
                    name="Revenue.xlsx",
                ),
            )

    async def test_unselected_used_range_registers_blank_separated_tables(
        self,
    ) -> None:
        repository = InMemoryArtifactRepository()
        blob_store = InMemoryBlobStore()
        artifact_service = ArtifactVersionService(
            repository=repository,
            blob_store=blob_store,
        )
        snapshot = self._two_table_snapshot()
        service = WorkbookContextService(
            artifact_service=artifact_service,
            dataset_catalog=InMemoryDatasetCatalog(),
        )
        context = SpreadsheetContext(
            workbook_id="workbook-1",
            workbook_name="Two tables",
            client_revision=1,
            worksheet_id="sheet-1",
            worksheet_name="Sheet1",
            used_range="Sheet1!A1:E5",
            snapshot_range="Sheet1!A1:E5",
            snapshot_hash=canonical_snapshot_hash(snapshot),
            snapshot=snapshot,
        )

        resolved = await service.resolve(
            user_id="user-1",
            workspace_id="workspace-1",
            context=context,
            active_artifact=None,
        )

        self.assertEqual(len(resolved.dataset_handles), 2)
        self.assertEqual(len(resolved.dataset_artifact_version_ids), 2)
        self.assertEqual(
            {
                handle.locator.range_a1
                for handle in resolved.dataset_handles
            },
            {"'Sheet1'!A1:B2", "'Sheet1'!D4:E5"},
        )
        handles_by_range = {
            handle.locator.range_a1: handle
            for handle in resolved.dataset_handles
        }
        revenue_handle = handles_by_range["'Sheet1'!A1:B2"]
        cost_handle = handles_by_range["'Sheet1'!D4:E5"]
        self.assertEqual(
            tuple((column.label, column.type) for column in revenue_handle.columns),
            (
                ("revenue", DatasetColumnType.NUMBER),
                ("region", DatasetColumnType.STRING),
            ),
        )
        self.assertEqual(
            tuple((column.label, column.type) for column in cost_handle.columns),
            (
                ("cost", DatasetColumnType.NUMBER),
                ("team", DatasetColumnType.STRING),
            ),
        )
        self.assertEqual(revenue_handle.row_count, 1)
        self.assertEqual(cost_handle.row_count, 1)

        hydrated_by_range: dict[str, TabularDataset] = {}
        for handle in resolved.dataset_handles:
            content = await blob_store.download(handle.storage.blob)
            hydrated_by_range[handle.locator.range_a1] = (
                TabularDataset.model_validate_json(gzip.decompress(content))
            )
        self.assertEqual(
            hydrated_by_range["'Sheet1'!A1:B2"].rows,
            ({"c_0001": 50_001, "c_0002": "west"},),
        )
        self.assertEqual(
            hydrated_by_range["'Sheet1'!D4:E5"].rows,
            ({"c_0001": 20_000, "c_0002": "ops"},),
        )

    async def test_selected_range_stays_one_authoritative_dataset(self) -> None:
        repository = InMemoryArtifactRepository()
        artifact_service = ArtifactVersionService(
            repository=repository,
            blob_store=InMemoryBlobStore(),
        )
        snapshot = self._two_table_snapshot()
        service = WorkbookContextService(
            artifact_service=artifact_service,
            dataset_catalog=InMemoryDatasetCatalog(),
        )
        context = SpreadsheetContext(
            workbook_id="workbook-1",
            workbook_name="Two tables",
            client_revision=1,
            worksheet_id="sheet-1",
            worksheet_name="Sheet1",
            selected_range="Sheet1!A1:E5",
            used_range="Sheet1!A1:E5",
            snapshot_range="Sheet1!A1:E5",
            snapshot_hash=canonical_snapshot_hash(snapshot),
            snapshot=snapshot,
        )

        resolved = await service.resolve(
            user_id="user-1",
            workspace_id="workspace-1",
            context=context,
            active_artifact=None,
        )

        self.assertEqual(len(resolved.dataset_handles), 1)
        self.assertEqual(
            resolved.dataset_handle.locator.range_a1,
            "Sheet1!A1:E5",
        )

    async def test_detected_table_count_is_bounded(self) -> None:
        repository = InMemoryArtifactRepository()
        artifact_service = ArtifactVersionService(
            repository=repository,
            blob_store=InMemoryBlobStore(),
        )
        snapshot = self._two_table_snapshot()
        service = WorkbookContextService(
            artifact_service=artifact_service,
            dataset_catalog=InMemoryDatasetCatalog(),
            limits=WorkbookContextLimits(max_datasets=1),
        )
        context = SpreadsheetContext(
            workbook_id="workbook-1",
            workbook_name="Two tables",
            client_revision=1,
            worksheet_id="sheet-1",
            worksheet_name="Sheet1",
            used_range="Sheet1!A1:E5",
            snapshot_range="Sheet1!A1:E5",
            snapshot_hash=canonical_snapshot_hash(snapshot),
            snapshot=snapshot,
        )

        with self.assertRaisesRegex(
            WorkbookContextTooLargeError,
            "detected dataset limit",
        ):
            await service.resolve(
                user_id="user-1",
                workspace_id="workspace-1",
                context=context,
                active_artifact=None,
            )


class CloudinaryArtifactBlobStoreTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _timeout_store() -> CloudinaryArtifactBlobStore:
        return CloudinaryArtifactBlobStore(
            CloudinaryBlobStoreConfig(
                cloud_name="test-cloud",
                api_key="test-key",
                api_secret="test-secret",
                request_timeout_seconds=0.01,
            )
        )

    @staticmethod
    def _sample_upload() -> BlobUpload:
        content = b"a,b\n1,2\n"
        return BlobUpload(
            object_key="docmind/user/workspace/artifact/version/data.csv",
            content=content,
            content_type="text/csv",
            filename="data.csv",
            sha256=hashlib.sha256(content).hexdigest(),
        )

    @staticmethod
    def _sample_reference() -> BlobReference:
        upload = CloudinaryArtifactBlobStoreTests._sample_upload()
        return BlobReference(
            provider="cloudinary",
            object_key=upload.object_key,
            resource_type="raw",
            delivery_type="authenticated",
            content_type=upload.content_type,
            filename=upload.filename,
            byte_count=upload.byte_count,
            sha256=upload.sha256,
        )

    async def _assert_bounded_timeout(
        self,
        operation: Any,
        release: threading.Event,
    ) -> None:
        started_at = asyncio.get_running_loop().time()
        try:
            with self.assertRaises(BlobStoreUnavailableError):
                await operation()
        finally:
            release.set()
        self.assertLess(
            asyncio.get_running_loop().time() - started_at,
            0.5,
        )

    async def test_upload_sdk_timeout_is_bounded_and_ambiguous(self) -> None:
        release = threading.Event()

        def blocked_upload(*args: Any, **kwargs: Any) -> dict[str, Any]:
            release.wait(timeout=1)
            return {}

        with (
            patch(
                "scripts.data_analysis_agent.runtime.storage.cloudinary."
                "cloudinary.uploader.upload",
                side_effect=blocked_upload,
            ),
            patch(
                "scripts.data_analysis_agent.runtime.storage.cloudinary."
                "cloudinary.api.resource",
            ) as provider_recovery,
        ):
            await self._assert_bounded_timeout(
                lambda: self._timeout_store().upload(self._sample_upload()),
                release,
            )

        provider_recovery.assert_not_called()

    async def test_stat_sdk_timeout_is_bounded(self) -> None:
        release = threading.Event()

        def blocked_stat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            release.wait(timeout=1)
            return {}

        with patch(
            "scripts.data_analysis_agent.runtime.storage.cloudinary."
            "cloudinary.api.resource",
            side_effect=blocked_stat,
        ):
            await self._assert_bounded_timeout(
                lambda: self._timeout_store().stat(self._sample_reference()),
                release,
            )

    async def test_conflict_recovery_sdk_timeout_is_bounded(self) -> None:
        release = threading.Event()

        def blocked_recovery(*args: Any, **kwargs: Any) -> dict[str, Any]:
            release.wait(timeout=1)
            return {}

        with (
            patch(
                "scripts.data_analysis_agent.runtime.storage.cloudinary."
                "cloudinary.uploader.upload",
                side_effect=AlreadyExists("already exists"),
            ),
            patch(
                "scripts.data_analysis_agent.runtime.storage.cloudinary."
                "cloudinary.api.resource",
                side_effect=blocked_recovery,
            ),
        ):
            await self._assert_bounded_timeout(
                lambda: self._timeout_store().upload(self._sample_upload()),
                release,
            )

    async def test_delete_sdk_timeout_is_bounded(self) -> None:
        release = threading.Event()

        def blocked_delete(*args: Any, **kwargs: Any) -> dict[str, Any]:
            release.wait(timeout=1)
            return {"result": "ok"}

        with patch(
            "scripts.data_analysis_agent.runtime.storage.cloudinary."
            "cloudinary.uploader.destroy",
            side_effect=blocked_delete,
        ):
            await self._assert_bounded_timeout(
                lambda: self._timeout_store().delete(self._sample_reference()),
                release,
            )

    async def test_upload_is_raw_authenticated_and_immutable(self) -> None:
        content = b"a,b\n1,2\n"
        upload = BlobUpload(
            object_key="docmind/user/workspace/artifact/version/data.csv",
            content=content,
            content_type="text/csv",
            filename="data.csv",
            sha256=hashlib.sha256(content).hexdigest(),
        )
        provider_result = {
            "public_id": upload.object_key,
            "asset_id": "asset-1",
            "version": 7,
            "resource_type": "raw",
            "type": "authenticated",
            "bytes": len(content),
        }
        store = CloudinaryArtifactBlobStore(
            CloudinaryBlobStoreConfig(
                cloud_name="test-cloud",
                api_key="test-key",
                api_secret="test-secret",
            )
        )

        with patch(
            "scripts.data_analysis_agent.runtime.storage.cloudinary."
            "cloudinary.uploader.upload",
            return_value=provider_result,
        ) as provider_upload:
            reference = await store.upload(upload)

        self.assertEqual(reference.object_key, upload.object_key)
        self.assertEqual(reference.sha256, upload.sha256)
        _, kwargs = provider_upload.call_args
        self.assertEqual(kwargs["resource_type"], "raw")
        self.assertEqual(kwargs["type"], "authenticated")
        self.assertFalse(kwargs["overwrite"])
        self.assertFalse(kwargs["unique_filename"])
        self.assertFalse(kwargs["use_filename"])
        self.assertEqual(kwargs["timeout"], 30.0)

    async def test_upload_retry_recovers_matching_immutable_object(self) -> None:
        content = b"a,b\n1,2\n"
        upload = BlobUpload(
            object_key="docmind/user/workspace/artifact/version/data.csv",
            content=content,
            content_type="text/csv",
            filename="data.csv",
            sha256=hashlib.sha256(content).hexdigest(),
        )
        provider_result = {
            "public_id": upload.object_key,
            "asset_id": "asset-1",
            "version": 7,
            "resource_type": "raw",
            "type": "authenticated",
            "bytes": len(content),
            "created_at": "2026-07-30T10:00:00Z",
            "context": {"custom": {"sha256": upload.sha256}},
        }
        store = CloudinaryArtifactBlobStore(
            CloudinaryBlobStoreConfig(
                cloud_name="test-cloud",
                api_key="test-key",
                api_secret="test-secret",
            )
        )

        with (
            patch(
                "scripts.data_analysis_agent.runtime.storage.cloudinary."
                "cloudinary.uploader.upload",
                side_effect=AlreadyExists("already exists"),
            ),
            patch(
                "scripts.data_analysis_agent.runtime.storage.cloudinary."
                "cloudinary.api.resource",
                return_value=provider_result,
            ) as provider_stat,
        ):
            reference = await store.upload(upload)

        self.assertEqual(reference.object_key, upload.object_key)
        self.assertEqual(reference.sha256, upload.sha256)
        self.assertEqual(reference.provider_version, "7")
        provider_stat.assert_called_once_with(
            upload.object_key,
            resource_type="raw",
            type="authenticated",
            context=True,
            timeout=30.0,
        )
