from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

from ..models.artifacts import BlobReference


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class BlobStoreError(RuntimeError):
    """Base exception for provider-neutral artifact blob operations."""


class BlobNotFoundError(BlobStoreError):
    """The referenced object does not exist in the backing store."""


class BlobConflictError(BlobStoreError):
    """An immutable object key already exists."""


class BlobIntegrityError(BlobStoreError):
    """Stored content does not match its declared size or checksum."""


class BlobStoreUnavailableError(BlobStoreError):
    """The provider could not complete an otherwise valid operation."""


@dataclass(frozen=True, slots=True)
class BlobUpload:
    """Immutable bytes and metadata supplied to a blob-store adapter."""

    object_key: str
    content: bytes
    content_type: str
    filename: str
    sha256: str
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.object_key.strip():
            raise ValueError("object_key is required")
        if not self.content:
            raise ValueError("content must not be empty")
        if not self.content_type.strip():
            raise ValueError("content_type is required")
        if not self.filename.strip():
            raise ValueError("filename is required")
        normalized_hash = self.sha256.casefold()
        if not _SHA256_PATTERN.fullmatch(normalized_hash):
            raise ValueError("sha256 must be a lowercase hexadecimal digest")
        if hashlib.sha256(self.content).hexdigest() != normalized_hash:
            raise BlobIntegrityError("upload bytes do not match the supplied checksum")
        object.__setattr__(self, "sha256", normalized_hash)
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def byte_count(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class BlobStat:
    """Small provider response used for post-upload integrity checks."""

    object_key: str
    byte_count: int
    provider_version: str | None = None
    provider_asset_id: str | None = None
    etag: str | None = None
    stored_sha256: str | None = None


@runtime_checkable
class ArtifactBlobStore(Protocol):
    """Provider-neutral asynchronous storage boundary for immutable artifacts."""

    async def upload(self, upload: BlobUpload) -> BlobReference: ...

    async def stat(self, reference: BlobReference) -> BlobStat: ...

    async def download(
        self,
        reference: BlobReference,
        *,
        max_bytes: int | None = None,
    ) -> bytes: ...

    async def generate_signed_download(
        self,
        reference: BlobReference,
        *,
        expires_in_seconds: int = 900,
    ) -> str: ...

    async def signed_download_url(
        self,
        reference: BlobReference,
        *,
        expires_in_seconds: int = 900,
    ) -> str: ...

    async def verify_checksum(self, reference: BlobReference) -> None: ...

    async def delete(self, reference: BlobReference) -> bool: ...
