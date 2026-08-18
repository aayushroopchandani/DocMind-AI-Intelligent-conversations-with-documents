"""Reading a published result back for patch compilation (Phase 9.11 → 9.10).

The execution bundle is immutable and content-addressed, so the patch compiler
reads it rather than trusting anything held in memory since the run finished —
which may have been minutes ago, on a different worker, after a restart.

Rows arrive as an iterator. That is the whole point of this module: the compiler
streams the result into payload chunks and a rolling hash, so nothing here is
allowed to hand it a fully materialized table.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from ...models.executions import AnalysisExecution
from ...models.plans import PlanColumn
from ...storage.base import ArtifactBlobStore, BlobStoreError
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


class BlobExecutionResultReader:
    """Reads the `rows` member of a published bundle out of blob storage."""

    def __init__(
        self,
        store: ArtifactBlobStore,
        *,
        max_bytes: int | None = None,
    ) -> None:
        self._store = store
        self._max_bytes = max_bytes

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


__all__ = [
    "BlobExecutionResultReader",
    "ExecutionResultReader",
    "ResultRows",
    "ResultUnavailableError",
]
