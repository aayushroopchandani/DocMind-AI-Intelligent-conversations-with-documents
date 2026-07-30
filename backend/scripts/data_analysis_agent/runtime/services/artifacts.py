from __future__ import annotations

import asyncio
import hashlib
import re
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Mapping
from uuid import uuid4

from pydantic import JsonValue

from ..models.artifacts import (
    ArtifactSource,
    ArtifactVersion,
    ArtifactVersionStatus,
    BlobReference,
    WorkspaceArtifact,
    WorkspaceArtifactType,
)
from ..repositories.artifacts import (
    ArtifactRepository,
    ArtifactRepositoryError,
    ArtifactUploadLeaseConflictError,
    ArtifactVersionDraft,
    new_workspace_artifact,
)
from ..storage.base import (
    ArtifactBlobStore,
    BlobIntegrityError,
    BlobNotFoundError,
    BlobStoreError,
    BlobStoreUnavailableError,
    BlobUpload,
)
from ..storage.validation import (
    ArtifactKind,
    ArtifactValidationError,
    ArtifactValidationLimits,
    ArtifactValidationProfile,
    ValidatedArtifact,
    validate_artifact,
)


_OBJECT_SEGMENT_CHARACTER = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_OBJECT_SEGMENT_LENGTH = 64
_ALLOWED_KINDS: dict[WorkspaceArtifactType, frozenset[ArtifactKind]] = {
    WorkspaceArtifactType.SPREADSHEET: frozenset({"xlsx", "json", "csv"}),
    WorkspaceArtifactType.CSV: frozenset({"csv"}),
    WorkspaceArtifactType.XLSX: frozenset({"xlsx"}),
    WorkspaceArtifactType.JSON: frozenset({"json"}),
    WorkspaceArtifactType.DATASET: frozenset({"csv", "json", "xlsx"}),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ArtifactServiceError(RuntimeError):
    """Base exception raised by the artifact version lifecycle."""


class ArtifactVersionInProgressError(ArtifactServiceError):
    """An idempotent request found the same version still uploading."""


class ArtifactUploadFailedError(ArtifactServiceError):
    """Blob upload or verification failed and the version was marked failed."""

    def __init__(self, version_id: str, code: str, message: str) -> None:
        super().__init__(message)
        self.version_id = version_id
        self.code = code


class ArtifactFinalizationPendingError(ArtifactServiceError):
    """The blob exists but MongoDB finalization must be retried/reconciled."""

    def __init__(self, version_id: str) -> None:
        super().__init__(
            "Artifact bytes were stored, but version finalization is pending."
        )
        self.version_id = version_id


class ArtifactReconciliationDisposition(str, Enum):
    FINALIZED = "finalized"
    POINTER_REPAIRED = "pointer_repaired"
    FAILED = "failed"
    PENDING = "pending"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class ArtifactReconciliationResult:
    version_id: str
    disposition: ArtifactReconciliationDisposition
    version: ArtifactVersion


@dataclass(frozen=True, slots=True)
class ArtifactReconciliationSummary:
    inspected: int = 0
    finalized: int = 0
    pointer_repaired: int = 0
    failed: int = 0
    pending: int = 0
    unchanged: int = 0


@dataclass(frozen=True, slots=True)
class ArtifactServiceConfig:
    object_prefix: str = "docmind"
    reconciliation_concurrency: int = 4
    upload_lease_seconds: float = 90.0
    upload_heartbeat_seconds: float = 20.0
    validation_limits: ArtifactValidationLimits = field(
        default_factory=ArtifactValidationLimits
    )

    def __post_init__(self) -> None:
        normalized_prefix = self.object_prefix.strip("/")
        if (
            not normalized_prefix
            or len(normalized_prefix) > 100
            or ".." in normalized_prefix.split("/")
            or any(
                not _safe_object_segment(part)
                for part in normalized_prefix.split("/")
            )
        ):
            raise ValueError("object_prefix must contain normalized safe path segments")
        if not 1 <= self.reconciliation_concurrency <= 32:
            raise ValueError(
                "reconciliation_concurrency must be between 1 and 32"
            )
        if self.upload_lease_seconds < 10:
            raise ValueError("upload_lease_seconds must be at least 10")
        if not 0 < self.upload_heartbeat_seconds < self.upload_lease_seconds:
            raise ValueError(
                "upload heartbeat must occur before upload lease expiry"
            )
        object.__setattr__(self, "object_prefix", normalized_prefix)


@dataclass(frozen=True, slots=True)
class CreateArtifactVersion:
    user_id: str
    workspace_id: str
    artifact_id: str
    artifact_type: WorkspaceArtifactType
    artifact_name: str
    source: ArtifactSource
    filename: str
    content_type: str | None
    content: bytes
    version_id: str | None = None
    parent_version_id: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    validation_profile: ArtifactValidationProfile = (
        ArtifactValidationProfile.UNTRUSTED_UPLOAD
    )


@dataclass(frozen=True, slots=True)
class ArtifactUploadResult:
    artifact: WorkspaceArtifact
    version: ArtifactVersion


class ArtifactVersionService:
    """Validate → reserve → upload → verify → finalize an artifact version."""

    def __init__(
        self,
        *,
        repository: ArtifactRepository,
        blob_store: ArtifactBlobStore,
        config: ArtifactServiceConfig | None = None,
    ) -> None:
        self._repository = repository
        self._blob_store = blob_store
        self._config = config or ArtifactServiceConfig()

    @asynccontextmanager
    async def _owned_upload(
        self,
        version: ArtifactVersion,
        *,
        owner_prefix: str,
    ) -> AsyncIterator[tuple[ArtifactVersion, str]]:
        owner_id = f"{owner_prefix}:{uuid4()}"
        now = _utc_now()
        try:
            claimed = await self._repository.claim_upload_lease(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                version_id=version.version_id,
                upload_owner_id=owner_id,
                current_time=now,
                lease_expires_at=now
                + timedelta(seconds=self._config.upload_lease_seconds),
            )
        except ArtifactUploadLeaseConflictError as exc:
            raise ArtifactVersionInProgressError(
                "This artifact version is already being uploaded."
            ) from exc
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat_upload_lease(
                version=claimed,
                owner_id=owner_id,
                stop=stop_heartbeat,
            ),
            name=f"artifact-upload-heartbeat:{claimed.version_id}",
        )
        try:
            yield claimed, owner_id
        finally:
            stop_heartbeat.set()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            try:
                await self._repository.release_upload_lease(
                    user_id=claimed.user_id,
                    workspace_id=claimed.workspace_id,
                    version_id=claimed.version_id,
                    upload_owner_id=owner_id,
                    current_time=_utc_now(),
                )
            except ArtifactRepositoryError:
                # Ready/failed writes clear ownership atomically. If an
                # uploading lease cannot be released, its expiry still makes
                # the reservation recoverable without unsafe takeover.
                pass

    async def _heartbeat_upload_lease(
        self,
        *,
        version: ArtifactVersion,
        owner_id: str,
        stop: asyncio.Event,
    ) -> None:
        try:
            while True:
                try:
                    await asyncio.wait_for(
                        stop.wait(),
                        timeout=self._config.upload_heartbeat_seconds,
                    )
                    return
                except TimeoutError:
                    pass
                now = _utc_now()
                await self._repository.renew_upload_lease(
                    user_id=version.user_id,
                    workspace_id=version.workspace_id,
                    version_id=version.version_id,
                    upload_owner_id=owner_id,
                    current_time=now,
                    lease_expires_at=now
                    + timedelta(seconds=self._config.upload_lease_seconds),
                )
        except asyncio.CancelledError:
            raise
        except ArtifactRepositoryError:
            # The caller remains fenced by owner+unexpired-lease checks on
            # every state mutation. A failed heartbeat therefore cannot let a
            # stale process finalize after another process takes ownership.
            return

    async def reconcile_stale_versions(
        self,
        *,
        stale_before: datetime,
        limit: int = 25,
    ) -> ArtifactReconciliationSummary:
        """Reconcile a bounded batch without reconstructing source bytes."""

        if stale_before.tzinfo is None:
            raise ValueError("stale_before must be timezone-aware")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        # Reserve one slot for pointer repair so a permanently transient
        # uploading queue cannot starve already-ready metadata forever.
        upload_limit = limit if limit == 1 else limit - 1
        uploading = await self._repository.list_stale_uploading_versions(
            stale_before=stale_before.astimezone(timezone.utc),
            current_time=_utc_now(),
            limit=upload_limit,
        )
        remaining = limit - len(uploading)
        pointer_repairs = (
            await self._repository.list_ready_versions_with_stale_pointer(
                limit=remaining,
            )
            if remaining > 0
            else ()
        )
        versions = tuple(
            {
                version.version_id: version
                for version in (*uploading, *pointer_repairs)
            }.values()
        )
        semaphore = asyncio.Semaphore(self._config.reconciliation_concurrency)

        async def reconcile_bounded(
            version: ArtifactVersion,
        ) -> ArtifactReconciliationResult:
            async with semaphore:
                return await self.reconcile_version(version)

        results = await asyncio.gather(
            *(reconcile_bounded(version) for version in versions)
        )
        counts = {
            disposition: sum(
                result.disposition == disposition for result in results
            )
            for disposition in ArtifactReconciliationDisposition
        }
        return ArtifactReconciliationSummary(
            inspected=len(results),
            finalized=counts[ArtifactReconciliationDisposition.FINALIZED],
            pointer_repaired=counts[
                ArtifactReconciliationDisposition.POINTER_REPAIRED
            ],
            failed=counts[ArtifactReconciliationDisposition.FAILED],
            pending=counts[ArtifactReconciliationDisposition.PENDING],
            unchanged=counts[ArtifactReconciliationDisposition.UNCHANGED],
        )

    async def reconcile_version(
        self,
        version: ArtifactVersion,
    ) -> ArtifactReconciliationResult:
        """Recover one known reservation using only immutable metadata."""

        try:
            current = await self._repository.get_version(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                version_id=version.version_id,
            )
        except ArtifactRepositoryError:
            return self._reconciliation_result(
                version,
                ArtifactReconciliationDisposition.PENDING,
            )
        if current is None:
            return self._reconciliation_result(
                version,
                ArtifactReconciliationDisposition.UNCHANGED,
            )
        if current.status == ArtifactVersionStatus.READY:
            return await self._repair_ready_pointer(current)
        if current.status != ArtifactVersionStatus.UPLOADING:
            return self._reconciliation_result(
                current,
                ArtifactReconciliationDisposition.UNCHANGED,
            )
        try:
            async with self._owned_upload(
                current,
                owner_prefix="artifact-reconcile",
            ) as (claimed, owner_id):
                if claimed.blob is not None:
                    return await self._reconcile_recorded_blob(
                        claimed,
                        owner_id=owner_id,
                    )
                return await self._recover_unrecorded_blob(
                    claimed,
                    owner_id=owner_id,
                )
        except ArtifactVersionInProgressError:
            return self._reconciliation_result(
                current,
                ArtifactReconciliationDisposition.PENDING,
            )

    async def _repair_ready_pointer(
        self,
        version: ArtifactVersion,
    ) -> ArtifactReconciliationResult:
        assert version.blob is not None
        try:
            artifact_before = await self._repository.get_artifact(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                artifact_id=version.artifact_id,
            )
            ready = await self._repository.mark_version_ready(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                version_id=version.version_id,
                blob=version.blob,
            )
            artifact_after = await self._repository.get_artifact(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                artifact_id=version.artifact_id,
            )
        except ArtifactRepositoryError:
            return self._reconciliation_result(
                version,
                ArtifactReconciliationDisposition.PENDING,
            )
        repaired = (
            artifact_before is not None
            and artifact_before.current_version_id != version.version_id
            and artifact_after is not None
            and artifact_after.current_version_id == version.version_id
        )
        return self._reconciliation_result(
            ready,
            (
                ArtifactReconciliationDisposition.POINTER_REPAIRED
                if repaired
                else ArtifactReconciliationDisposition.UNCHANGED
            ),
        )

    async def _reconcile_recorded_blob(
        self,
        version: ArtifactVersion,
        *,
        owner_id: str,
    ) -> ArtifactReconciliationResult:
        assert version.blob is not None
        try:
            await self._blob_store.verify_checksum(version.blob)
        except (BlobIntegrityError, BlobNotFoundError) as exc:
            return await self._mark_reconciliation_failed(
                version,
                exc,
                owner_id=owner_id,
            )
        except (BlobStoreError, TimeoutError):
            return self._reconciliation_result(
                version,
                ArtifactReconciliationDisposition.PENDING,
            )
        return await self._finalize_reconciled_blob(
            version,
            version.blob,
            owner_id=owner_id,
        )

    async def _recover_unrecorded_blob(
        self,
        version: ArtifactVersion,
        *,
        owner_id: str,
    ) -> ArtifactReconciliationResult:
        expected = self._unrecorded_blob_reference(version)
        try:
            stat = await self._blob_store.stat(expected)
            if stat.object_key != expected.object_key:
                raise BlobIntegrityError(
                    "stored object key does not match its reservation"
                )
            if stat.byte_count != version.byte_count:
                raise BlobIntegrityError(
                    "stored object size does not match its reservation"
                )
            if (
                stat.stored_sha256 is not None
                and stat.stored_sha256 != version.content_hash
            ):
                raise BlobIntegrityError(
                    "stored object checksum does not match its reservation"
                )
            # Provider metadata can be absent or stale. Hashing the immutable
            # object establishes exact content identity before it is adopted;
            # no speculative cleanup is needed when identity cannot be proven.
            await self._blob_store.verify_checksum(expected)
        except BlobNotFoundError as exc:
            return await self._mark_reconciliation_failed(
                version,
                exc,
                owner_id=owner_id,
            )
        except BlobIntegrityError as exc:
            return await self._mark_reconciliation_failed(
                version,
                exc,
                owner_id=owner_id,
            )
        except (BlobStoreError, TimeoutError):
            return self._reconciliation_result(
                version,
                ArtifactReconciliationDisposition.PENDING,
            )

        recovered = expected.model_copy(
            update={
                "provider_asset_id": stat.provider_asset_id,
                "provider_version": stat.provider_version,
            }
        )
        try:
            recorded = await self._repository.record_uploaded_blob(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                version_id=version.version_id,
                blob=recovered,
                upload_owner_id=owner_id,
            )
        except ArtifactRepositoryError:
            try:
                latest = await self._repository.get_version(
                    user_id=version.user_id,
                    workspace_id=version.workspace_id,
                    version_id=version.version_id,
                )
            except ArtifactRepositoryError:
                latest = None
            if latest is None:
                return self._reconciliation_result(
                    version,
                    ArtifactReconciliationDisposition.PENDING,
                )
            if latest.status == ArtifactVersionStatus.READY:
                return await self._repair_ready_pointer(latest)
            if (
                latest.status != ArtifactVersionStatus.UPLOADING
                or latest.blob != recovered
            ):
                return self._reconciliation_result(
                    latest,
                    ArtifactReconciliationDisposition.PENDING,
                )
            recorded = latest
        return await self._finalize_reconciled_blob(
            recorded,
            recovered,
            owner_id=owner_id,
        )

    async def _finalize_reconciled_blob(
        self,
        version: ArtifactVersion,
        blob: BlobReference,
        *,
        owner_id: str,
    ) -> ArtifactReconciliationResult:
        try:
            ready = await self._repository.mark_version_ready(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                version_id=version.version_id,
                blob=blob,
                upload_owner_id=owner_id,
            )
        except ArtifactRepositoryError:
            try:
                latest = await self._repository.get_version(
                    user_id=version.user_id,
                    workspace_id=version.workspace_id,
                    version_id=version.version_id,
                )
            except ArtifactRepositoryError:
                latest = None
            if (
                latest is not None
                and latest.status == ArtifactVersionStatus.READY
            ):
                repaired = await self._repair_ready_pointer(latest)
                if (
                    repaired.disposition
                    == ArtifactReconciliationDisposition.UNCHANGED
                ):
                    return self._reconciliation_result(
                        repaired.version,
                        ArtifactReconciliationDisposition.FINALIZED,
                    )
                return repaired
            return self._reconciliation_result(
                version,
                ArtifactReconciliationDisposition.PENDING,
            )
        return self._reconciliation_result(
            ready,
            ArtifactReconciliationDisposition.FINALIZED,
        )

    async def _mark_reconciliation_failed(
        self,
        version: ArtifactVersion,
        error: BlobStoreError,
        *,
        owner_id: str,
    ) -> ArtifactReconciliationResult:
        code, message = _safe_storage_failure(error)
        try:
            failed = await self._repository.mark_version_failed(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                version_id=version.version_id,
                error_code=code,
                error_message=message,
                upload_owner_id=owner_id,
            )
        except ArtifactRepositoryError:
            return self._reconciliation_result(
                version,
                ArtifactReconciliationDisposition.PENDING,
            )
        return self._reconciliation_result(
            failed,
            ArtifactReconciliationDisposition.FAILED,
        )

    def _unrecorded_blob_reference(
        self,
        version: ArtifactVersion,
    ) -> BlobReference:
        return BlobReference(
            provider="cloudinary",
            object_key=self._object_key(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                artifact_id=version.artifact_id,
                version_id=version.version_id,
                filename=version.filename,
            ),
            resource_type="raw",
            delivery_type="authenticated",
            content_type=version.content_type,
            filename=version.filename,
            byte_count=version.byte_count,
            sha256=version.content_hash,
        )

    @staticmethod
    def _reconciliation_result(
        version: ArtifactVersion,
        disposition: ArtifactReconciliationDisposition,
    ) -> ArtifactReconciliationResult:
        return ArtifactReconciliationResult(
            version_id=version.version_id,
            disposition=disposition,
            version=version,
        )

    async def create_version(
        self,
        request: CreateArtifactVersion,
    ) -> ArtifactUploadResult:
        validated = await asyncio.to_thread(
            validate_artifact,
            request.content,
            filename=request.filename,
            content_type=request.content_type,
            limits=self._config.validation_limits,
            profile=request.validation_profile,
        )
        self._validate_artifact_kind(request.artifact_type, validated.kind)

        artifact = new_workspace_artifact(
            artifact_id=request.artifact_id,
            user_id=request.user_id,
            workspace_id=request.workspace_id,
            artifact_type=request.artifact_type,
            name=request.artifact_name,
            source=request.source,
        )
        artifact = await self._repository.ensure_artifact(artifact)

        version_id = request.version_id or str(uuid4())
        metadata = {
            **dict(request.metadata),
            "artifact_kind": validated.kind,
            "compressed": validated.is_compressed,
            # Artifact source is first-write provenance; retain the producer of
            # each immutable version separately for auditability.
            "version_source": request.source.value,
            "validation_profile": request.validation_profile.value,
        }
        reservation = await self._repository.reserve_version(
            ArtifactVersionDraft(
                version_id=version_id,
                artifact_id=request.artifact_id,
                user_id=request.user_id,
                workspace_id=request.workspace_id,
                content_hash=validated.sha256,
                byte_count=validated.bytes,
                content_type=validated.content_type,
                filename=validated.filename,
                parent_version_id=request.parent_version_id,
                metadata=metadata,
            )
        )
        version = reservation.version
        if version.status == ArtifactVersionStatus.READY:
            assert version.blob is not None
            return await self._promote_ready_replay(
                artifact=artifact,
                version=version,
            )
        if version.status != ArtifactVersionStatus.UPLOADING:
            raise ArtifactUploadFailedError(
                version.version_id,
                version.error_code or "artifact_version_unavailable",
                version.error_message
                or "This artifact version cannot be uploaded again.",
            )
        async with self._owned_upload(
            version,
            owner_prefix="artifact-upload",
        ) as (claimed, owner_id):
            return await self._upload_owned_version(
                request=request,
                validated=validated,
                artifact=artifact,
                version=claimed,
                owner_id=owner_id,
            )

    async def _upload_owned_version(
        self,
        *,
        request: CreateArtifactVersion,
        validated: ValidatedArtifact,
        artifact: WorkspaceArtifact,
        version: ArtifactVersion,
        owner_id: str,
    ) -> ArtifactUploadResult:
        if version.blob is not None:
            return await self._verify_and_finalize(
                artifact=artifact,
                version=version,
                blob=version.blob,
                owner_id=owner_id,
            )
        object_key = self._object_key(
            user_id=request.user_id,
            workspace_id=request.workspace_id,
            artifact_id=request.artifact_id,
            version_id=version.version_id,
            filename=validated.filename,
        )
        try:
            blob = await self._blob_store.upload(
                BlobUpload(
                    object_key=object_key,
                    content=request.content,
                    content_type=validated.content_type,
                    filename=validated.filename,
                    sha256=validated.sha256,
                    metadata={
                        "user_id": request.user_id,
                        "workspace_id": request.workspace_id,
                        "artifact_id": request.artifact_id,
                        "artifact_version_id": version.version_id,
                    },
                )
            )
        except (BlobStoreUnavailableError, TimeoutError) as exc:
            # Timeouts and transport failures are ambiguous: the immutable
            # provider object may have been accepted before the response was
            # lost. Keep the deterministic reservation resumable so a retry
            # can recover that object instead of permanently orphaning it.
            raise ArtifactFinalizationPendingError(version.version_id) from exc
        except BlobStoreError as exc:
            code, message = _safe_storage_failure(exc)
            await self._best_effort_mark_failed(
                version=version,
                code=code,
                message=message,
                owner_id=owner_id,
            )
            raise ArtifactUploadFailedError(version.version_id, code, message) from exc

        if blob.sha256 != validated.sha256 or blob.byte_count != validated.bytes:
            integrity_error = BlobIntegrityError(
                "blob reference does not match validated artifact bytes"
            )
            await self._fail_integrity_check(
                version=version,
                blob=blob,
                error=integrity_error,
                owner_id=owner_id,
            )

        try:
            version = await self._repository.record_uploaded_blob(
                user_id=request.user_id,
                workspace_id=request.workspace_id,
                version_id=version.version_id,
                blob=blob,
                upload_owner_id=owner_id,
            )
        except ArtifactRepositoryError as exc:
            try:
                stored = await self._repository.get_version(
                    user_id=request.user_id,
                    workspace_id=request.workspace_id,
                    version_id=version.version_id,
                )
                if (
                    stored is not None
                    and stored.status == ArtifactVersionStatus.UPLOADING
                    and stored.blob == blob
                ):
                    version = stored
                else:
                    raise ArtifactFinalizationPendingError(version.version_id) from exc
            except ArtifactFinalizationPendingError:
                raise
            except ArtifactRepositoryError as retry_exc:
                raise ArtifactFinalizationPendingError(version.version_id) from retry_exc

        return await self._verify_and_finalize(
            artifact=artifact,
            version=version,
            blob=blob,
            owner_id=owner_id,
        )

    async def _promote_ready_replay(
        self,
        *,
        artifact: WorkspaceArtifact,
        version: ArtifactVersion,
    ) -> ArtifactUploadResult:
        """Idempotently repair an artifact pointer after a partial finalize."""

        assert version.blob is not None
        try:
            promoted = await self._repository.mark_version_ready(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                version_id=version.version_id,
                blob=version.blob,
            )
        except ArtifactRepositoryError as exc:
            raise ArtifactFinalizationPendingError(version.version_id) from exc
        refreshed_artifact = await self._load_artifact(
            artifact=artifact,
            current_version_id=promoted.version_id,
        )
        return ArtifactUploadResult(
            artifact=refreshed_artifact,
            version=promoted,
        )

    async def _verify_and_finalize(
        self,
        *,
        artifact: WorkspaceArtifact,
        version: ArtifactVersion,
        blob: BlobReference,
        owner_id: str,
    ) -> ArtifactUploadResult:
        try:
            await self._blob_store.verify_checksum(blob)
        except (BlobIntegrityError, BlobNotFoundError) as exc:
            await self._fail_integrity_check(
                version=version,
                blob=blob,
                error=exc,
                owner_id=owner_id,
            )
        except BlobStoreError as exc:
            # The uploaded reference is durable now. A transient verification
            # outage is resumable with the same version ID and must not cause
            # a second upload or delete an object that may be healthy.
            raise ArtifactFinalizationPendingError(version.version_id) from exc

        try:
            ready_version = await self._repository.mark_version_ready(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                version_id=version.version_id,
                blob=blob,
                upload_owner_id=owner_id,
            )
        except ArtifactRepositoryError as exc:
            # Never delete here: the ready update may have succeeded while its
            # acknowledgement or current-version promotion failed. A retry or
            # reconciliation worker can safely finalize the immutable blob.
            try:
                stored = await self._repository.get_version(
                    user_id=version.user_id,
                    workspace_id=version.workspace_id,
                    version_id=version.version_id,
                )
                if (
                    stored is not None
                    and stored.status == ArtifactVersionStatus.READY
                ):
                    ready_version = await self._repository.mark_version_ready(
                        user_id=version.user_id,
                        workspace_id=version.workspace_id,
                        version_id=version.version_id,
                        blob=blob,
                        upload_owner_id=owner_id,
                    )
                else:
                    raise ArtifactFinalizationPendingError(version.version_id) from exc
            except ArtifactFinalizationPendingError:
                raise
            except ArtifactRepositoryError as retry_exc:
                raise ArtifactFinalizationPendingError(version.version_id) from retry_exc

        refreshed_artifact = await self._load_artifact(
            artifact=artifact,
            current_version_id=ready_version.version_id,
        )
        return ArtifactUploadResult(
            artifact=refreshed_artifact,
            version=ready_version,
        )

    async def _fail_integrity_check(
        self,
        *,
        version: ArtifactVersion,
        blob: BlobReference,
        error: BlobStoreError,
        owner_id: str,
    ) -> None:
        code, message = _safe_storage_failure(error)
        try:
            await self._repository.mark_version_failed(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                version_id=version.version_id,
                error_code=code,
                error_message=message,
                upload_owner_id=owner_id,
            )
        except ArtifactRepositoryError as exc:
            # Ownership may have moved after a heartbeat failure. Never delete
            # until the fenced failed transition proves this process still
            # owns the object lifecycle.
            raise ArtifactFinalizationPendingError(version.version_id) from exc
        await self._best_effort_delete(blob)
        raise ArtifactUploadFailedError(version.version_id, code, message) from error

    async def signed_download_url(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        expires_in_seconds: int = 900,
    ) -> str:
        version = await self._require_ready_version(
            user_id=user_id,
            workspace_id=workspace_id,
            version_id=version_id,
        )
        assert version.blob is not None  # guaranteed by ArtifactVersion model
        return await self._blob_store.generate_signed_download(
            version.blob,
            expires_in_seconds=expires_in_seconds,
        )

    async def download(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        max_bytes: int | None = None,
    ) -> bytes:
        version = await self._require_ready_version(
            user_id=user_id,
            workspace_id=workspace_id,
            version_id=version_id,
        )
        assert version.blob is not None
        return await self._blob_store.download(
            version.blob,
            max_bytes=max_bytes,
        )

    async def get_ready_version(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
    ) -> ArtifactVersion:
        """Resolve immutable metadata through the same tenant/readiness guard."""

        return await self._require_ready_version(
            user_id=user_id,
            workspace_id=workspace_id,
            version_id=version_id,
        )

    async def _require_ready_version(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
    ) -> ArtifactVersion:
        version = await self._repository.get_version(
            user_id=user_id,
            workspace_id=workspace_id,
            version_id=version_id,
        )
        if version is None:
            raise ArtifactServiceError("Artifact version was not found.")
        if version.status != ArtifactVersionStatus.READY or version.blob is None:
            raise ArtifactServiceError("Artifact version is not ready.")
        return version

    async def _load_artifact(
        self,
        *,
        artifact: WorkspaceArtifact,
        current_version_id: str,
    ) -> WorkspaceArtifact:
        try:
            stored = await self._repository.get_artifact(
                user_id=artifact.user_id,
                workspace_id=artifact.workspace_id,
                artifact_id=artifact.artifact_id,
            )
        except ArtifactRepositoryError:
            stored = None
        if stored is not None:
            return stored
        # Finalization already returned a ready version. A transient follow-up
        # read must not turn that success into an apparent failed upload.
        now = datetime.now(timezone.utc)
        return WorkspaceArtifact.model_validate(
            artifact.model_copy(
                update={
                    "current_version_id": current_version_id,
                    "updated_at": now,
                }
            ).model_dump()
        )

    async def _best_effort_delete(self, blob: BlobReference) -> None:
        try:
            await self._blob_store.delete(blob)
        except BlobStoreError:
            return

    async def _best_effort_mark_failed(
        self,
        *,
        version: ArtifactVersion,
        code: str,
        message: str,
        owner_id: str,
    ) -> None:
        try:
            await self._repository.mark_version_failed(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                version_id=version.version_id,
                error_code=code,
                error_message=message,
                upload_owner_id=owner_id,
            )
        except ArtifactRepositoryError:
            # A stale uploading record is recoverable by reconciliation; it is
            # preferable to masking the original provider error.
            return

    def _object_key(
        self,
        *,
        user_id: str,
        workspace_id: str,
        artifact_id: str,
        version_id: str,
        filename: str,
    ) -> str:
        return "/".join(
            (
                self._config.object_prefix,
                _object_segment(user_id),
                _object_segment(workspace_id),
                _object_segment(artifact_id),
                _object_segment(version_id),
                _object_segment(filename),
            )
        )

    @staticmethod
    def _validate_artifact_kind(
        artifact_type: WorkspaceArtifactType,
        kind: ArtifactKind,
    ) -> None:
        allowed = _ALLOWED_KINDS.get(artifact_type)
        if allowed is None or kind not in allowed:
            raise ArtifactValidationError(
                "artifact_type_mismatch",
                f"{kind.upper()} content cannot be stored as {artifact_type.value}.",
            )


def _object_segment(value: str) -> str:
    normalized = _OBJECT_SEGMENT_CHARACTER.sub("_", value).strip("._-")
    if not normalized:
        normalized = "item"
    if normalized != value or len(normalized) > _MAX_OBJECT_SEGMENT_LENGTH:
        digest_suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        stem, dot, extension = normalized.rpartition(".")
        if dot and stem and extension.isalnum() and len(extension) <= 12:
            available = _MAX_OBJECT_SEGMENT_LENGTH - len(extension) - 14
            normalized = f"{stem[:available]}-{digest_suffix}.{extension}"
        else:
            normalized = (
                f"{normalized[:_MAX_OBJECT_SEGMENT_LENGTH - 13]}-{digest_suffix}"
            )
    return normalized


def _safe_object_segment(value: str) -> bool:
    return bool(value) and _object_segment(value) == value


def _safe_storage_failure(exc: BlobStoreError) -> tuple[str, str]:
    if isinstance(exc, BlobIntegrityError):
        return (
            "artifact_integrity_failed",
            "Artifact storage verification failed.",
        )
    if isinstance(exc, BlobNotFoundError):
        return (
            "artifact_blob_missing",
            "The uploaded artifact could not be verified in storage.",
        )
    return (
        "artifact_storage_failed",
        "Artifact bytes could not be stored.",
    )
