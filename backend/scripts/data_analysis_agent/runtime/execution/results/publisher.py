"""Publish a validated result bundle to durable storage (Phase 9.9.3).

Ordering matters more than anything else here. The bundle is uploaded *before*
the execution record is marked succeeded, so the two possible crash points both
leave a recoverable state:

* crash after upload, before commit — the objects exist but no record points at
  them; reconciliation finds the reserved execution and either completes or
  deletes them;
* crash after commit — everything the record claims is already durable.

Committing first would allow the opposite: a record promising a result that was
never stored, which nothing can repair.

Object keys are content-addressed by execution key, so re-publishing an
identical result overwrites identical bytes instead of accumulating garbage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import polars as pl

from ....runtime.models.artifacts import BlobReference
from ....runtime.models.executions import ResultArtifacts
from ....runtime.models.plans import PlanColumn
from ....runtime.storage.base import ArtifactBlobStore, BlobStoreError, BlobUpload
from .serialization import build_schema_manifest, encode_json, encode_rows
from ..contracts import ExecutionFailureCode


RESULT_OBJECT_PREFIX = "analysis/results"


class ResultPublicationError(RuntimeError):
    """The result bundle could not be stored durably."""

    def __init__(self, code: ExecutionFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class PublishedBundle:
    artifacts: ResultArtifacts
    total_bytes: int


def object_key(*, workspace_id: str, execution_key: str, name: str) -> str:
    """Return the immutable object key for one bundle member."""

    return f"{RESULT_OBJECT_PREFIX}/{workspace_id}/{execution_key}/{name}"


async def publish_result(
    *,
    store: ArtifactBlobStore,
    workspace_id: str,
    execution_key: str,
    frame: pl.DataFrame,
    columns: tuple[PlanColumn, ...],
    content_hash: str,
    lineage: dict[str, Any],
    preview: dict[str, Any],
) -> PublishedBundle:
    """Upload the four-member bundle and return its references."""

    rows = encode_rows(frame, columns)
    manifest = encode_json(
        build_schema_manifest(
            columns,
            row_count=frame.height,
            content_hash=content_hash,
        )
    )
    members = (
        ("result.csv.gz", rows, "application/gzip"),
        ("result.schema.json", manifest, "application/json"),
        ("result.lineage.json", encode_json(lineage), "application/json"),
        ("result.preview.json", encode_json(preview), "application/json"),
    )

    references: dict[str, BlobReference] = {}
    total = 0
    for name, payload, content_type in members:
        reference = await _upload(
            store=store,
            workspace_id=workspace_id,
            execution_key=execution_key,
            name=name,
            payload=payload,
            content_type=content_type,
        )
        references[name] = reference
        total += reference.byte_count

    return PublishedBundle(
        artifacts=ResultArtifacts(
            rows=references["result.csv.gz"],
            schema_manifest=references["result.schema.json"],
            lineage=references["result.lineage.json"],
            preview=references["result.preview.json"],
        ),
        total_bytes=total,
    )


async def _upload(
    *,
    store: ArtifactBlobStore,
    workspace_id: str,
    execution_key: str,
    name: str,
    payload: bytes,
    content_type: str,
) -> BlobReference:
    import hashlib

    digest = hashlib.sha256(payload).hexdigest()
    upload = BlobUpload(
        object_key=object_key(
            workspace_id=workspace_id,
            execution_key=execution_key,
            name=name,
        ),
        content=payload,
        content_type=content_type,
        filename=name,
        sha256=digest,
        metadata={"execution_key": execution_key, "member": name},
    )
    try:
        reference = await store.upload(upload)
    except BlobStoreError as error:
        raise ResultPublicationError(
            ExecutionFailureCode.OUTPUT_TOO_LARGE
            if "size" in str(error).casefold()
            else ExecutionFailureCode.ENGINE_CRASHED,
            f"result member '{name}' could not be stored: {error}",
        ) from error

    if reference.sha256 != digest:
        # A provider that returned a different digest stored different bytes.
        # Never mark such an asset ready (9.9 acceptance criteria).
        raise ResultPublicationError(
            ExecutionFailureCode.SCHEMA_MISMATCH,
            f"stored checksum for '{name}' does not match the uploaded bytes",
        )
    return reference


async def verify_bundle(
    *,
    store: ArtifactBlobStore,
    artifacts: ResultArtifacts,
) -> None:
    """Re-check every member against the provider before trusting the bundle."""

    for reference in artifacts.references():
        try:
            await store.verify_checksum(reference)
        except BlobStoreError as error:
            raise ResultPublicationError(
                ExecutionFailureCode.SCHEMA_MISMATCH,
                f"stored result member '{reference.filename}' failed "
                f"verification: {error}",
            ) from error


__all__ = [
    "RESULT_OBJECT_PREFIX",
    "PublishedBundle",
    "ResultPublicationError",
    "object_key",
    "publish_result",
    "verify_bundle",
]
