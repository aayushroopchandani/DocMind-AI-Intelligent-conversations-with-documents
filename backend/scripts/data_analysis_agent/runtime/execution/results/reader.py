"""Reading a published result back for patch compilation (Phase 9.11 → 9.10).

The execution bundle is immutable and content-addressed, so the patch compiler
reads it rather than trusting anything held in memory since the run finished —
which may have been minutes ago, on a different worker, after a restart.

Rows arrive as an iterator. That is the whole point of this module: the compiler
streams the result into payload chunks and a rolling hash, so nothing here is
allowed to hand it a fully materialized table.

The preview is the one member read whole, because it is bounded by construction
(9.9.1) and already redacted through the privacy gateway at publish time. Read
paths therefore hand it back as stored rather than re-deriving a second, possibly
different, redaction policy.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from ...models.executions import AnalysisExecution
from ...models.plans import PlanColumn
from ...storage.base import ArtifactBlobStore, BlobStoreError
from .previews import MAX_PREVIEW_BYTES
from .serialization import ResultSerializationError, iter_result_rows


class ResultUnavailableError(RuntimeError):
    """The published result cannot be read back."""


@dataclass(frozen=True, slots=True)
class ResultRows:
    """A published result, ready to stream."""

    columns: tuple[PlanColumn, ...]
    row_count: int
    rows: Iterator[tuple[Any, ...]]


class ExecutionResultReader(Protocol):
    async def read(self, execution: AnalysisExecution) -> ResultRows: ...


class ExecutionPreviewReader(Protocol):
    async def read_preview(
        self,
        execution: AnalysisExecution,
    ) -> dict[str, Any]: ...


class BlobExecutionResultReader:
    """Reads members of a published bundle back out of blob storage.

    One instance serves both readers: the streaming `rows` path used by patch
    compilation and the bounded `preview` path used by the read API. They have
    different byte budgets, so the preview cap is deliberately independent of
    the row cap rather than inheriting it.
    """

    def __init__(
        self,
        store: ArtifactBlobStore,
        *,
        max_bytes: int | None = None,
        max_preview_bytes: int = MAX_PREVIEW_BYTES,
    ) -> None:
        self._store = store
        self._max_bytes = max_bytes
        self._max_preview_bytes = max_preview_bytes

    async def read(self, execution: AnalysisExecution) -> ResultRows:
        if execution.artifacts is None:
            raise ResultUnavailableError(
                "this execution has no published result bundle"
            )
        try:
            payload = await self._store.download(
                execution.artifacts.rows,
                max_bytes=self._max_bytes,
            )
        except BlobStoreError as error:
            raise ResultUnavailableError(
                "the published result could not be downloaded"
            ) from error
        columns = execution.result_columns
        if not columns:
            raise ResultUnavailableError(
                "this execution did not record a result schema"
            )
        try:
            rows = iter_result_rows(payload, columns)
        except ResultSerializationError as error:
            raise ResultUnavailableError(str(error)) from error
        return ResultRows(
            columns=columns,
            row_count=execution.metrics.output_rows,
            rows=rows,
        )

    async def read_preview(
        self,
        execution: AnalysisExecution,
    ) -> dict[str, Any]:
        """Return the stored preview for `execution`, as it was published."""

        if execution.artifacts is None:
            raise ResultUnavailableError(
                "this execution has no published result bundle"
            )
        try:
            payload = await self._store.download(
                execution.artifacts.preview,
                max_bytes=self._max_preview_bytes,
            )
        except BlobStoreError as error:
            raise ResultUnavailableError(
                "the stored preview could not be downloaded"
            ) from error
        try:
            preview = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResultUnavailableError(
                "the stored preview is not readable JSON"
            ) from error
        if not isinstance(preview, dict):
            raise ResultUnavailableError(
                "the stored preview is not a preview document"
            )
        return preview


__all__ = [
    "BlobExecutionResultReader",
    "ExecutionPreviewReader",
    "ExecutionResultReader",
    "ResultRows",
    "ResultUnavailableError",
]
