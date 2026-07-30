from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BlobProvider(str, Enum):
    CLOUDINARY = "cloudinary"


class BlobResourceType(str, Enum):
    RAW = "raw"
    IMAGE = "image"


class BlobDeliveryType(str, Enum):
    AUTHENTICATED = "authenticated"
    PRIVATE = "private"


class WorkspaceArtifactType(str, Enum):
    SPREADSHEET = "spreadsheet"
    PDF = "pdf"
    CSV = "csv"
    XLSX = "xlsx"
    JSON = "json"
    DATASET = "dataset"
    CHART = "chart"
    REPORT = "report"
    DASHBOARD = "dashboard"


class ArtifactSource(str, Enum):
    CREATED = "created"
    UPLOADED = "uploaded"
    IMPORTED = "imported"
    GENERATED = "generated"


class ArtifactVersionStatus(str, Enum):
    UPLOADING = "uploading"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class BlobReference(BaseModel):
    """Provider-neutral identity for an immutable external object."""

    provider: BlobProvider
    object_key: str = Field(min_length=1, max_length=1024)
    provider_asset_id: str | None = Field(default=None, max_length=512)
    provider_version: str | None = Field(default=None, max_length=256)
    resource_type: BlobResourceType = BlobResourceType.RAW
    delivery_type: BlobDeliveryType = BlobDeliveryType.AUTHENTICATED
    content_type: str = Field(min_length=1, max_length=255)
    filename: str = Field(min_length=1, max_length=255)
    byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    @field_validator("sha256", mode="before")
    @classmethod
    def normalize_sha256(cls, value: object) -> str:
        normalized = str(value or "").strip().casefold()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        return normalized

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
            raise ValueError("filename must not contain a path")
        return value


class WorkspaceArtifact(BaseModel):
    """Workspace-level artifact metadata; bytes live in artifact versions."""

    schema_version: Literal[1] = 1
    artifact_id: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    artifact_type: WorkspaceArtifactType
    name: str = Field(min_length=1, max_length=255)
    source: ArtifactSource
    current_version_id: str | None = Field(default=None, max_length=200)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    @field_validator(
        "artifact_id",
        "user_id",
        "workspace_id",
        "current_version_id",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER_RE.fullmatch(value):
            raise ValueError("identifier contains unsupported characters")
        return value

    @model_validator(mode="after")
    def validate_timestamps(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


class ArtifactVersion(BaseModel):
    """One immutable artifact version and its two-stage upload state."""

    schema_version: Literal[1] = 1
    version_id: str = Field(min_length=1, max_length=200)
    artifact_id: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    version_number: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)
    content_type: str = Field(min_length=1, max_length=255)
    filename: str = Field(min_length=1, max_length=255)
    status: ArtifactVersionStatus
    blob: BlobReference | None = None
    upload_owner_id: str | None = Field(default=None, max_length=200)
    upload_lease_expires_at: datetime | None = None
    upload_attempt: int = Field(default=0, ge=0)
    parent_version_id: str | None = Field(default=None, max_length=200)
    # Legacy records leave this unset. New reservations persist whether the
    # caller explicitly chose a parent or asked the repository to resolve the
    # then-current version, so an idempotent replay cannot change that intent.
    parent_version_is_explicit: bool | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=120)
    error_message: str | None = Field(default=None, max_length=1000)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    ready_at: datetime | None = None
    failed_at: datetime | None = None

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    @field_validator(
        "version_id",
        "artifact_id",
        "user_id",
        "workspace_id",
        "parent_version_id",
        "upload_owner_id",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER_RE.fullmatch(value):
            raise ValueError("identifier contains unsupported characters")
        return value

    @field_validator("content_hash", mode="before")
    @classmethod
    def normalize_content_hash(cls, value: object) -> str:
        normalized = str(value or "").strip().casefold()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        return normalized

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
            raise ValueError("filename must not contain a path")
        return value

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if (
            self.parent_version_is_explicit is True
            and self.parent_version_id is None
        ):
            raise ValueError("an explicit parent version id is required")
        if (self.upload_owner_id is None) != (
            self.upload_lease_expires_at is None
        ):
            raise ValueError(
                "upload owner and upload lease expiry must be set together"
            )
        if self.upload_owner_id is not None and self.upload_attempt < 1:
            raise ValueError("owned uploads need upload_attempt >= 1")
        if (
            self.status != ArtifactVersionStatus.UPLOADING
            and self.upload_owner_id is not None
        ):
            raise ValueError("terminal artifact versions cannot retain an upload lease")
        if self.blob is not None:
            if self.blob.sha256 != self.content_hash:
                raise ValueError("blob checksum must match content_hash")
            if self.blob.byte_count != self.byte_count:
                raise ValueError("blob byte count must match artifact version")
        if self.status == ArtifactVersionStatus.READY:
            if self.blob is None or self.ready_at is None:
                raise ValueError("ready artifact versions need a blob and ready_at")
            if self.error_code or self.error_message or self.failed_at:
                raise ValueError("ready artifact versions cannot contain failure details")
        elif self.status == ArtifactVersionStatus.FAILED:
            if self.failed_at is None or not self.error_code:
                raise ValueError("failed artifact versions need failed_at and error_code")
            if self.ready_at is not None:
                raise ValueError("failed artifact versions cannot have ready_at")
        elif self.ready_at is not None or self.failed_at is not None:
            raise ValueError("non-terminal artifact versions cannot have terminal timestamps")
        return self


__all__ = [
    "ArtifactSource",
    "ArtifactVersion",
    "ArtifactVersionStatus",
    "BlobDeliveryType",
    "BlobProvider",
    "BlobReference",
    "BlobResourceType",
    "WorkspaceArtifact",
    "WorkspaceArtifactType",
]
