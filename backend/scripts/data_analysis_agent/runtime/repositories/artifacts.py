from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from pydantic import JsonValue, ValidationError

from db.mongodb import get_db

from ..models.artifacts import (
    ArtifactSource,
    ArtifactVersion,
    ArtifactVersionStatus,
    BlobReference,
    WorkspaceArtifact,
    WorkspaceArtifactType,
)

try:
    from pymongo import ReturnDocument
except ImportError:  # pragma: no cover
    ReturnDocument = None  # type: ignore[assignment]


class ArtifactRepositoryError(RuntimeError):
    """Artifact metadata could not be read or persisted."""


class ArtifactNotFoundError(ArtifactRepositoryError):
    """A tenant-scoped artifact or artifact version was not found."""


class ArtifactStateConflictError(ArtifactRepositoryError):
    """A version transition raced with, or contradicted, existing state."""


class ArtifactUploadLeaseConflictError(ArtifactStateConflictError):
    """An upload reservation is actively owned by another process."""


@dataclass(frozen=True, slots=True)
class ArtifactVersionDraft:
    """Metadata required to reserve one immutable version number."""

    version_id: str
    artifact_id: str
    user_id: str
    workspace_id: str
    content_hash: str
    byte_count: int
    content_type: str
    filename: str
    parent_version_id: str | None = None
    metadata: Mapping[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class ArtifactVersionReservation:
    version: ArtifactVersion
    created: bool


class ArtifactRepository(Protocol):
    async def ensure_artifact(
        self,
        artifact: WorkspaceArtifact,
    ) -> WorkspaceArtifact: ...

    async def reserve_version(
        self,
        draft: ArtifactVersionDraft,
    ) -> ArtifactVersionReservation: ...

    async def get_artifact(
        self,
        *,
        user_id: str,
        workspace_id: str,
        artifact_id: str,
    ) -> WorkspaceArtifact | None: ...

    async def get_version(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
    ) -> ArtifactVersion | None: ...

    async def list_stale_uploading_versions(
        self,
        *,
        stale_before: datetime,
        current_time: datetime,
        limit: int,
    ) -> tuple[ArtifactVersion, ...]: ...

    async def list_ready_versions_with_stale_pointer(
        self,
        *,
        limit: int,
    ) -> tuple[ArtifactVersion, ...]: ...

    async def record_uploaded_blob(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        blob: BlobReference,
        upload_owner_id: str | None = None,
    ) -> ArtifactVersion: ...

    async def mark_version_ready(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        blob: BlobReference,
        upload_owner_id: str | None = None,
    ) -> ArtifactVersion: ...

    async def mark_version_failed(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        error_code: str,
        error_message: str,
        upload_owner_id: str | None = None,
    ) -> ArtifactVersion: ...

    async def claim_upload_lease(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        upload_owner_id: str,
        current_time: datetime,
        lease_expires_at: datetime,
    ) -> ArtifactVersion: ...

    async def renew_upload_lease(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        upload_owner_id: str,
        current_time: datetime,
        lease_expires_at: datetime,
    ) -> ArtifactVersion: ...

    async def release_upload_lease(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        upload_owner_id: str,
        current_time: datetime,
    ) -> ArtifactVersion: ...


class MongoArtifactRepository:
    """Tenant-scoped MongoDB repository for artifact metadata and versions."""

    artifacts_collection_name = "workspace_artifacts"
    versions_collection_name = "artifact_versions"

    async def ensure_artifact(
        self,
        artifact: WorkspaceArtifact,
    ) -> WorkspaceArtifact:
        collection = get_db()[self.artifacts_collection_name]
        query = self._artifact_query(
            user_id=artifact.user_id,
            workspace_id=artifact.workspace_id,
            artifact_id=artifact.artifact_id,
        )
        document = artifact.model_dump(mode="python")
        # Internal counters are intentionally absent from the domain model.
        document["version_counter"] = 0
        document["current_version_number"] = 0
        try:
            await collection.update_one(
                query,
                {"$setOnInsert": document},
                upsert=True,
            )
            stored = await collection.find_one(query, {"_id": 0})
        except Exception as exc:
            raise ArtifactRepositoryError(
                "workspace artifact could not be persisted"
            ) from exc
        parsed = self._parse_artifact(stored)
        if parsed.artifact_type != artifact.artifact_type:
            raise ArtifactStateConflictError(
                "artifact identity already exists with a different type"
            )
        # Source records how this stable artifact identity first entered the
        # workspace. It is intentionally first-write-wins: later immutable
        # versions may be uploaded, imported, or server-generated without
        # rewriting the artifact's original provenance.
        return parsed

    async def reserve_version(
        self,
        draft: ArtifactVersionDraft,
    ) -> ArtifactVersionReservation:
        if ReturnDocument is None:  # pragma: no cover
            raise ArtifactRepositoryError("pymongo is required")
        artifacts = get_db()[self.artifacts_collection_name]
        versions = get_db()[self.versions_collection_name]
        artifact_query = self._artifact_query(
            user_id=draft.user_id,
            workspace_id=draft.workspace_id,
            artifact_id=draft.artifact_id,
        )
        try:
            existing = await versions.find_one(
                self._version_query(
                    user_id=draft.user_id,
                    workspace_id=draft.workspace_id,
                    version_id=draft.version_id,
                ),
                {"_id": 0},
            )
            if existing is not None:
                parsed_existing = self._parse_version(existing)
                self._assert_same_draft(parsed_existing, draft)
                return ArtifactVersionReservation(
                    version=parsed_existing,
                    created=False,
                )

            artifact_document = await artifacts.find_one_and_update(
                artifact_query,
                {
                    "$inc": {"version_counter": 1},
                    "$set": {"updated_at": _utc_now()},
                },
                return_document=ReturnDocument.AFTER,
                projection={"_id": 0},
            )
            if artifact_document is None:
                raise ArtifactNotFoundError("workspace artifact was not found")

            version_number = int(artifact_document.get("version_counter") or 0)
            parent_version_id = (
                draft.parent_version_id
                if draft.parent_version_id is not None
                else artifact_document.get("current_version_id")
            )
            if parent_version_id is not None:
                await self._require_parent_version(
                    user_id=draft.user_id,
                    workspace_id=draft.workspace_id,
                    artifact_id=draft.artifact_id,
                    version_id=str(parent_version_id),
                )
            version = ArtifactVersion(
                version_id=draft.version_id,
                artifact_id=draft.artifact_id,
                user_id=draft.user_id,
                workspace_id=draft.workspace_id,
                version_number=version_number,
                content_hash=draft.content_hash,
                byte_count=draft.byte_count,
                content_type=draft.content_type,
                filename=draft.filename,
                status=ArtifactVersionStatus.UPLOADING,
                parent_version_id=(
                    str(parent_version_id) if parent_version_id is not None else None
                ),
                parent_version_is_explicit=(
                    draft.parent_version_id is not None
                ),
                metadata=dict(draft.metadata or {}),
            )
            await versions.insert_one(version.model_dump(mode="python"))
            return ArtifactVersionReservation(version=version, created=True)
        except (
            ArtifactNotFoundError,
            ArtifactStateConflictError,
            ValidationError,
            ValueError,
        ):
            raise
        except Exception as exc:
            # A duplicate version insert can race after the initial lookup.
            try:
                existing = await versions.find_one(
                    self._version_query(
                        user_id=draft.user_id,
                        workspace_id=draft.workspace_id,
                        version_id=draft.version_id,
                    ),
                    {"_id": 0},
                )
            except Exception:
                existing = None
            if existing is not None:
                parsed_existing = self._parse_version(existing)
                self._assert_same_draft(parsed_existing, draft)
                return ArtifactVersionReservation(
                    version=parsed_existing,
                    created=False,
                )
            raise ArtifactRepositoryError(
                "artifact version could not be reserved"
            ) from exc

    async def get_artifact(
        self,
        *,
        user_id: str,
        workspace_id: str,
        artifact_id: str,
    ) -> WorkspaceArtifact | None:
        try:
            document = await get_db()[self.artifacts_collection_name].find_one(
                self._artifact_query(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    artifact_id=artifact_id,
                ),
                {"_id": 0},
            )
        except Exception as exc:
            raise ArtifactRepositoryError("workspace artifact could not be read") from exc
        return self._parse_artifact(document) if document is not None else None

    async def get_version(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
    ) -> ArtifactVersion | None:
        try:
            document = await get_db()[self.versions_collection_name].find_one(
                self._version_query(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    version_id=version_id,
                ),
                {"_id": 0},
            )
        except Exception as exc:
            raise ArtifactRepositoryError("artifact version could not be read") from exc
        return self._parse_version(document) if document is not None else None

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
        if ReturnDocument is None:  # pragma: no cover
            raise ArtifactRepositoryError("pymongo is required")
        if lease_expires_at <= current_time:
            raise ValueError("upload lease expiry must be in the future")
        collection = get_db()[self.versions_collection_name]
        query = self._version_query(
            user_id=user_id,
            workspace_id=workspace_id,
            version_id=version_id,
        )
        try:
            document = await collection.find_one_and_update(
                {
                    **query,
                    "status": ArtifactVersionStatus.UPLOADING.value,
                    "$or": [
                        {"upload_owner_id": None},
                        {"upload_owner_id": {"$exists": False}},
                        {"upload_lease_expires_at": None},
                        {"upload_lease_expires_at": {"$lte": current_time}},
                    ],
                },
                {
                    "$set": {
                        "upload_owner_id": upload_owner_id,
                        "upload_lease_expires_at": lease_expires_at,
                        "updated_at": current_time,
                    },
                    "$inc": {"upload_attempt": 1},
                },
                return_document=ReturnDocument.AFTER,
                projection={"_id": 0},
            )
            if document is None:
                document = await collection.find_one(query, {"_id": 0})
        except Exception as exc:
            raise ArtifactRepositoryError(
                "artifact upload lease could not be claimed"
            ) from exc
        if document is None:
            raise ArtifactNotFoundError("artifact version was not found")
        version = self._parse_version(document)
        if (
            version.status == ArtifactVersionStatus.UPLOADING
            and version.upload_owner_id == upload_owner_id
            and version.upload_lease_expires_at is not None
            and version.upload_lease_expires_at > current_time
        ):
            return version
        raise ArtifactUploadLeaseConflictError(
            "artifact upload is already owned or no longer uploadable"
        )

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
        if ReturnDocument is None:  # pragma: no cover
            raise ArtifactRepositoryError("pymongo is required")
        if lease_expires_at <= current_time:
            raise ValueError("upload lease expiry must be in the future")
        try:
            document = await get_db()[
                self.versions_collection_name
            ].find_one_and_update(
                {
                    **self._version_query(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        version_id=version_id,
                    ),
                    "status": ArtifactVersionStatus.UPLOADING.value,
                    "upload_owner_id": upload_owner_id,
                    "upload_lease_expires_at": {"$gt": current_time},
                },
                {
                    "$set": {
                        "upload_lease_expires_at": lease_expires_at,
                        "updated_at": current_time,
                    }
                },
                return_document=ReturnDocument.AFTER,
                projection={"_id": 0},
            )
        except Exception as exc:
            raise ArtifactRepositoryError(
                "artifact upload lease could not be renewed"
            ) from exc
        if document is None:
            raise ArtifactUploadLeaseConflictError(
                "artifact upload lease is no longer owned"
            )
        return self._parse_version(document)

    async def release_upload_lease(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        upload_owner_id: str,
        current_time: datetime,
    ) -> ArtifactVersion:
        if ReturnDocument is None:  # pragma: no cover
            raise ArtifactRepositoryError("pymongo is required")
        collection = get_db()[self.versions_collection_name]
        query = self._version_query(
            user_id=user_id,
            workspace_id=workspace_id,
            version_id=version_id,
        )
        try:
            document = await collection.find_one_and_update(
                {
                    **query,
                    "status": ArtifactVersionStatus.UPLOADING.value,
                    "upload_owner_id": upload_owner_id,
                },
                {
                    "$set": {"updated_at": current_time},
                    "$unset": {
                        "upload_owner_id": "",
                        "upload_lease_expires_at": "",
                    },
                },
                return_document=ReturnDocument.AFTER,
                projection={"_id": 0},
            )
            if document is None:
                document = await collection.find_one(query, {"_id": 0})
        except Exception as exc:
            raise ArtifactRepositoryError(
                "artifact upload lease could not be released"
            ) from exc
        if document is None:
            raise ArtifactNotFoundError("artifact version was not found")
        version = self._parse_version(document)
        if version.status != ArtifactVersionStatus.UPLOADING:
            return version
        if version.upload_owner_id is None:
            return version
        raise ArtifactUploadLeaseConflictError(
            "artifact upload lease is owned by another process"
        )

    async def list_stale_uploading_versions(
        self,
        *,
        stale_before: datetime,
        current_time: datetime,
        limit: int,
    ) -> tuple[ArtifactVersion, ...]:
        if stale_before.tzinfo is None:
            raise ValueError("stale_before must be timezone-aware")
        if current_time.tzinfo is None:
            raise ValueError("current_time must be timezone-aware")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        try:
            cursor = (
                get_db()[self.versions_collection_name]
                .find(
                    {
                        "status": ArtifactVersionStatus.UPLOADING.value,
                        "updated_at": {
                            "$lte": stale_before.astimezone(timezone.utc)
                        },
                        "$or": [
                            {"upload_owner_id": None},
                            {"upload_owner_id": {"$exists": False}},
                            {"upload_lease_expires_at": None},
                            {
                                "upload_lease_expires_at": {
                                    "$lte": current_time.astimezone(timezone.utc)
                                }
                            },
                        ],
                    },
                    {"_id": 0},
                )
                .sort([("updated_at", 1), ("version_id", 1)])
                .limit(limit)
            )
            documents = await cursor.to_list(length=limit)
        except Exception as exc:
            raise ArtifactRepositoryError(
                "stale artifact uploads could not be listed"
            ) from exc
        return tuple(self._parse_version(document) for document in documents)

    async def list_ready_versions_with_stale_pointer(
        self,
        *,
        limit: int,
    ) -> tuple[ArtifactVersion, ...]:
        """Find ready versions newer than their artifact's current pointer."""

        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "status": ArtifactVersionStatus.READY.value,
                }
            },
            {
                "$lookup": {
                    "from": self.artifacts_collection_name,
                    "let": {
                        "user_id": "$user_id",
                        "workspace_id": "$workspace_id",
                        "artifact_id": "$artifact_id",
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$user_id", "$$user_id"]},
                                        {
                                            "$eq": [
                                                "$workspace_id",
                                                "$$workspace_id",
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                "$artifact_id",
                                                "$$artifact_id",
                                            ]
                                        },
                                    ]
                                }
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "current_version_number": 1,
                            }
                        },
                    ],
                    "as": "parent_artifact",
                }
            },
            {"$unwind": "$parent_artifact"},
            {
                "$match": {
                    "$expr": {
                        "$lt": [
                            {
                                "$ifNull": [
                                    "$parent_artifact.current_version_number",
                                    0,
                                ]
                            },
                            "$version_number",
                        ]
                    }
                }
            },
            {"$sort": {"updated_at": 1, "version_id": 1}},
            {"$limit": limit},
            {"$project": {"_id": 0, "parent_artifact": 0}},
        ]
        try:
            documents = await get_db()[
                self.versions_collection_name
            ].aggregate(pipeline).to_list(length=limit)
        except Exception as exc:
            raise ArtifactRepositoryError(
                "ready artifact pointers could not be reconciled"
            ) from exc
        return tuple(self._parse_version(document) for document in documents)

    async def mark_version_ready(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        blob: BlobReference,
        upload_owner_id: str | None = None,
    ) -> ArtifactVersion:
        versions = get_db()[self.versions_collection_name]
        query = self._version_query(
            user_id=user_id,
            workspace_id=workspace_id,
            version_id=version_id,
        )
        now = _utc_now()
        ownership_filter = (
            {}
            if upload_owner_id is None
            else {
                "upload_owner_id": upload_owner_id,
                "upload_lease_expires_at": {"$gt": now},
            }
        )
        try:
            result = await versions.update_one(
                {
                    **query,
                    "status": ArtifactVersionStatus.UPLOADING.value,
                    **ownership_filter,
                },
                {
                    "$set": {
                        "status": ArtifactVersionStatus.READY.value,
                        "blob": blob.model_dump(mode="python"),
                        "ready_at": now,
                        "updated_at": now,
                    },
                    "$unset": {
                        "error_code": "",
                        "error_message": "",
                        "failed_at": "",
                        "upload_owner_id": "",
                        "upload_lease_expires_at": "",
                    },
                },
            )
            document = await versions.find_one(query, {"_id": 0})
        except Exception as exc:
            raise ArtifactRepositoryError(
                "artifact version could not be finalized"
            ) from exc
        if document is None:
            raise ArtifactNotFoundError("artifact version was not found")
        version = self._parse_version(document)
        if result.matched_count == 0:
            if version.status == ArtifactVersionStatus.READY and version.blob == blob:
                await self._promote_ready_version(version)
                return version
            raise ArtifactStateConflictError(
                f"artifact version cannot transition from {version.status.value} to ready"
            )

        await self._promote_ready_version(version)
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
        versions = get_db()[self.versions_collection_name]
        query = self._version_query(
            user_id=user_id,
            workspace_id=workspace_id,
            version_id=version_id,
        )
        now = _utc_now()
        ownership_filter = (
            {}
            if upload_owner_id is None
            else {
                "upload_owner_id": upload_owner_id,
                "upload_lease_expires_at": {"$gt": now},
            }
        )
        try:
            result = await versions.update_one(
                {
                    **query,
                    "status": ArtifactVersionStatus.UPLOADING.value,
                    **ownership_filter,
                    "$or": [
                        {"blob": {"$exists": False}},
                        {"blob": None},
                    ],
                },
                {
                    "$set": {
                        "blob": blob.model_dump(mode="python"),
                        "updated_at": now,
                    }
                },
            )
            document = await versions.find_one(query, {"_id": 0})
        except Exception as exc:
            raise ArtifactRepositoryError(
                "uploaded blob reference could not be recorded"
            ) from exc
        if document is None:
            raise ArtifactNotFoundError("artifact version was not found")
        version = self._parse_version(document)
        if result.matched_count == 0:
            if (
                version.status == ArtifactVersionStatus.UPLOADING
                and version.blob == blob
                and (
                    upload_owner_id is None
                    or (
                        version.upload_owner_id == upload_owner_id
                        and version.upload_lease_expires_at is not None
                        and version.upload_lease_expires_at > now
                    )
                )
            ):
                return version
            raise ArtifactStateConflictError(
                "artifact version cannot accept this uploaded blob"
            )
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
        versions = get_db()[self.versions_collection_name]
        query = self._version_query(
            user_id=user_id,
            workspace_id=workspace_id,
            version_id=version_id,
        )
        now = _utc_now()
        safe_code = error_code.strip()[:120] or "artifact_upload_failed"
        safe_message = error_message.strip()[:1000] or "Artifact upload failed."
        ownership_filter = (
            {}
            if upload_owner_id is None
            else {
                "upload_owner_id": upload_owner_id,
                "upload_lease_expires_at": {"$gt": now},
            }
        )
        try:
            result = await versions.update_one(
                {
                    **query,
                    "status": ArtifactVersionStatus.UPLOADING.value,
                    **ownership_filter,
                },
                {
                    "$set": {
                        "status": ArtifactVersionStatus.FAILED.value,
                        "error_code": safe_code,
                        "error_message": safe_message,
                        "failed_at": now,
                        "updated_at": now,
                    },
                    "$unset": {
                        "ready_at": "",
                        "blob": "",
                        "upload_owner_id": "",
                        "upload_lease_expires_at": "",
                    },
                },
            )
            document = await versions.find_one(query, {"_id": 0})
        except Exception as exc:
            raise ArtifactRepositoryError(
                "artifact version failure could not be recorded"
            ) from exc
        if document is None:
            raise ArtifactNotFoundError("artifact version was not found")
        version = self._parse_version(document)
        if result.matched_count == 0 and version.status != ArtifactVersionStatus.FAILED:
            raise ArtifactStateConflictError(
                f"artifact version cannot transition from {version.status.value} to failed"
            )
        return version

    async def _promote_ready_version(self, version: ArtifactVersion) -> None:
        artifacts = get_db()[self.artifacts_collection_name]
        query = {
            **self._artifact_query(
                user_id=version.user_id,
                workspace_id=version.workspace_id,
                artifact_id=version.artifact_id,
            ),
            "$or": [
                {"current_version_number": {"$lt": version.version_number}},
                {"current_version_number": {"$exists": False}},
            ],
        }
        try:
            result = await artifacts.update_one(
                query,
                {
                    "$set": {
                        "current_version_id": version.version_id,
                        "current_version_number": version.version_number,
                        "updated_at": _utc_now(),
                    }
                },
            )
            if result.matched_count == 0:
                existing = await artifacts.find_one(
                    self._artifact_query(
                        user_id=version.user_id,
                        workspace_id=version.workspace_id,
                        artifact_id=version.artifact_id,
                    ),
                    {"_id": 0, "current_version_number": 1},
                )
                if existing is None:
                    raise ArtifactNotFoundError("workspace artifact was not found")
                # A newer ready version already won the race; it must remain
                # current even if this older upload finished later.
                if int(existing.get("current_version_number") or 0) < (
                    version.version_number
                ):
                    raise ArtifactRepositoryError(
                        "ready artifact version could not be promoted"
                    )
        except ArtifactRepositoryError:
            raise
        except Exception as exc:
            raise ArtifactRepositoryError(
                "ready artifact version could not be promoted"
            ) from exc

    async def _require_parent_version(
        self,
        *,
        user_id: str,
        workspace_id: str,
        artifact_id: str,
        version_id: str,
    ) -> None:
        document = await get_db()[self.versions_collection_name].find_one(
            {
                **self._version_query(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    version_id=version_id,
                ),
                "artifact_id": artifact_id,
                "status": ArtifactVersionStatus.READY.value,
            },
            {"_id": 1},
        )
        if document is None:
            raise ArtifactStateConflictError(
                "parent artifact version does not exist or is not ready"
            )

    @staticmethod
    def _artifact_query(
        *,
        user_id: str,
        workspace_id: str,
        artifact_id: str,
    ) -> dict[str, str]:
        return {
            "user_id": user_id,
            "workspace_id": workspace_id,
            "artifact_id": artifact_id,
        }

    @staticmethod
    def _version_query(
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
    ) -> dict[str, str]:
        return {
            "user_id": user_id,
            "workspace_id": workspace_id,
            "version_id": version_id,
        }

    @staticmethod
    def _parse_artifact(document: Mapping[str, Any] | None) -> WorkspaceArtifact:
        if document is None:
            raise ArtifactNotFoundError("workspace artifact was not found")
        try:
            fields = WorkspaceArtifact.model_fields
            return WorkspaceArtifact.model_validate(
                _normalize_datetimes(
                    {key: value for key, value in document.items() if key in fields}
                )
            )
        except ValidationError as exc:
            raise ArtifactRepositoryError(
                "stored workspace artifact is invalid"
            ) from exc

    @staticmethod
    def _parse_version(document: Mapping[str, Any]) -> ArtifactVersion:
        try:
            return ArtifactVersion.model_validate(_normalize_datetimes(document))
        except ValidationError as exc:
            raise ArtifactRepositoryError(
                "stored artifact version is invalid"
            ) from exc

    @staticmethod
    def _assert_same_draft(
        version: ArtifactVersion,
        draft: ArtifactVersionDraft,
    ) -> None:
        if (
            version.artifact_id != draft.artifact_id
            or version.content_hash != draft.content_hash
            or version.byte_count != draft.byte_count
            or version.content_type != draft.content_type
            or version.filename != draft.filename
            or not _parent_request_matches(version, draft)
            or _canonical_metadata(version.metadata)
            != _canonical_metadata(draft.metadata)
        ):
            raise ArtifactStateConflictError(
                "version_id is already reserved for different content or provenance"
            )


def _parent_request_matches(
    version: ArtifactVersion,
    draft: ArtifactVersionDraft,
) -> bool:
    if version.parent_version_is_explicit is True:
        return (
            draft.parent_version_id is not None
            and version.parent_version_id == draft.parent_version_id
        )
    if version.parent_version_is_explicit is False:
        return draft.parent_version_id is None
    # Legacy versions did not record whether the resolved parent was explicit.
    # Preserve their replay compatibility while checking an explicit parent
    # supplied by a caller now.
    return (
        draft.parent_version_id is None
        or version.parent_version_id == draft.parent_version_id
    )


def _canonical_metadata(
    metadata: Mapping[str, JsonValue] | None,
) -> str:
    """Return a stable, type-preserving identity for version provenance."""

    try:
        return json.dumps(
            dict(metadata or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactStateConflictError(
            "artifact version metadata must be canonical JSON"
        ) from exc


def new_workspace_artifact(
    *,
    artifact_id: str,
    user_id: str,
    workspace_id: str,
    artifact_type: WorkspaceArtifactType,
    name: str,
    source: ArtifactSource,
) -> WorkspaceArtifact:
    """Small factory kept here so callers do not construct persistence fields."""

    return WorkspaceArtifact(
        artifact_id=artifact_id,
        user_id=user_id,
        workspace_id=workspace_id,
        artifact_type=artifact_type,
        name=name,
        source=source,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_datetimes(value: Any) -> Any:
    """Normalize nested BSON dates before domain validation and comparison."""

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, Mapping):
        return {key: _normalize_datetimes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_datetimes(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_datetimes(item) for item in value)
    return value
