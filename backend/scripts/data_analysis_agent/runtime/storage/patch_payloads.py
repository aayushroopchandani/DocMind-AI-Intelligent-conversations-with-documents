"""Where patch payload chunks live (Phase 9.10.3).

Payload blocks are immutable and addressed by patch revision, so recompiling the
same revision overwrites identical bytes rather than accumulating garbage, and a
chunk can never be silently swapped for another patch's data — the key contains
the patch it belongs to and the hash commits to its contents.

Nothing here mints a delivery URL. Chunks are read back through the
authenticated API, which keeps the tenant check on every byte and means no
signed URL is ever persisted in a patch.
"""

from __future__ import annotations

import hashlib

from ..models.artifacts import (
    BlobDeliveryType,
    BlobProvider,
    BlobReference,
    BlobResourceType,
)
from ..patches.operations import PayloadChunkReference
from .base import ArtifactBlobStore, BlobStoreError, BlobUpload


PATCH_OBJECT_PREFIX = "analysis/patches"


class PatchPayloadStorageError(RuntimeError):
    """A patch payload chunk could not be stored."""


def chunk_object_key(
    *,
    workspace_id: str,
    patch_id: str,
    patch_revision: int,
    index: int,
) -> str:
    return (
        f"{PATCH_OBJECT_PREFIX}/{workspace_id}/{patch_id}/"
        f"r{patch_revision}/chunk-{index:04d}.json"
    )


class BlobPayloadWriter:
    """Uploads one patch's payload chunks; satisfies `PayloadWriter`."""

    def __init__(
        self,
        store: ArtifactBlobStore,
        *,
        workspace_id: str,
        patch_id: str,
        patch_revision: int,
    ) -> None:
        self._store = store
        self._workspace_id = workspace_id
        self._patch_id = patch_id
        self._patch_revision = patch_revision

    async def write_chunk(self, *, index: int, data: bytes, sha256: str) -> str:
        key = chunk_object_key(
            workspace_id=self._workspace_id,
            patch_id=self._patch_id,
            patch_revision=self._patch_revision,
            index=index,
        )
        if hashlib.sha256(data).hexdigest() != sha256:  # pragma: no cover
            raise PatchPayloadStorageError(
                "chunk bytes do not match their checksum"
            )
        try:
            await self._store.upload(
                BlobUpload(
                    object_key=key,
                    content=data,
                    content_type="application/json",
                    filename=f"chunk-{index:04d}.json",
                    sha256=sha256,
                    metadata={
                        "patch_id": self._patch_id,
                        "patch_revision": str(self._patch_revision),
                        "chunk_index": str(index),
                    },
                )
            )
        except BlobStoreError as error:
            raise PatchPayloadStorageError(
                "patch payload chunk could not be stored"
            ) from error
        return key


class BlobPayloadReader:
    """Streams payload chunks back out for an authenticated client.

    Reading through the API rather than a signed URL keeps the tenant check on
    every byte, and means no delivery URL is ever persisted in a patch (9.10.3).
    Each chunk is verified against the checksum the patch hash commits to, so a
    swapped object is caught here rather than in the browser.
    """

    def __init__(
        self,
        store: ArtifactBlobStore,
        *,
        provider: BlobProvider = BlobProvider.CLOUDINARY,
    ) -> None:
        self._store = store
        self._provider = provider

    async def read_chunk(self, chunk: PayloadChunkReference) -> bytes:
        reference = BlobReference(
            provider=self._provider,
            object_key=chunk.object_key,
            resource_type=BlobResourceType.RAW,
            delivery_type=BlobDeliveryType.AUTHENTICATED,
            content_type="application/json",
            filename=f"chunk-{chunk.index:04d}.json",
            byte_count=chunk.byte_count,
            sha256=chunk.sha256,
        )
        try:
            data = await self._store.download(
                reference,
                max_bytes=chunk.byte_count,
            )
        except BlobStoreError as error:
            raise PatchPayloadStorageError(
                "patch payload chunk could not be read"
            ) from error
        if hashlib.sha256(data).hexdigest() != chunk.sha256:
            raise PatchPayloadStorageError(
                "stored payload chunk does not match the checksum the patch "
                "committed to"
            )
        return data


__all__ = [
    "PATCH_OBJECT_PREFIX",
    "BlobPayloadReader",
    "BlobPayloadWriter",
    "PatchPayloadStorageError",
    "chunk_object_key",
]
