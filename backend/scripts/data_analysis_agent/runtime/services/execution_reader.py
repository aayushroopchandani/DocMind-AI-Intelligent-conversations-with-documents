"""Reading a finished execution back for the client (Phase 9.14.1).

The write path addresses an execution by its deterministic key, because that is
what makes execution idempotent. A browser has no idea what that key is — it
holds a run — so this service is the read-side translation between the two.

Two rules shape it:

*Prefer the run's own pointer.* A run records `current_execution_id` when an
execution publishes. Following that pointer is exact. Falling back to "the newest
execution for this run" is only for records written before the pointer existed,
or for a run whose summary update was lost behind a completed execution.

*Never widen the tenant boundary.* Every lookup is filtered by `user_id` at the
repository, not merely checked afterwards, and an execution reached through a
pointer is still verified to belong to the run that named it. A stale or forged
pointer therefore reads as "not found" rather than as another tenant's result.

This service deliberately does not know about runs. The caller has already
resolved and authorized one — asking it to resolve a second time would be both
slower and a second place for the ownership rule to drift.
"""

from __future__ import annotations

from pydantic import ValidationError

from ..execution.results.previews import ResultPreview
from ..execution.results.reader import (
    ExecutionPreviewReader,
    ResultUnavailableError,
)
from ..models.executions import AnalysisExecution, ExecutionStatus
from ..repositories.executions import (
    ExecutionNotFoundError,
    ExecutionRepository,
)


class ExecutionReadService:
    """Tenant-scoped reads of durable execution records and their previews."""

    def __init__(
        self,
        *,
        repository: ExecutionRepository,
        preview_reader: ExecutionPreviewReader | None = None,
    ) -> None:
        self._repository = repository
        self._preview_reader = preview_reader

    @property
    def previews_available(self) -> bool:
        """Whether this deployment can serve stored previews at all."""

        return self._preview_reader is not None

    async def get_for_run(
        self,
        *,
        user_id: str,
        run_id: str,
        execution_id: str | None = None,
    ) -> AnalysisExecution:
        """Return the execution this run currently holds.

        `execution_id` is the run's own pointer when it has one. It is a hint,
        not an authority: a pointer that resolves to another run's execution is
        discarded rather than trusted.
        """

        if execution_id:
            execution = await self._repository.get_by_id(
                user_id=user_id,
                execution_id=execution_id,
            )
            if execution is not None and execution.run_id == run_id:
                return execution

        execution = await self._repository.get_for_run(
            user_id=user_id,
            run_id=run_id,
        )
        if execution is None:
            raise ExecutionNotFoundError(
                "this run has no execution record"
            )
        return execution

    async def read_preview(
        self,
        *,
        user_id: str,
        run_id: str,
        execution_id: str | None = None,
    ) -> tuple[AnalysisExecution, ResultPreview]:
        """Return the execution and the bounded preview it published.

        The preview is returned as it was stored, not re-derived: it was
        already bounded and redacted through the privacy gateway when the
        result was published (9.9.1), and deriving it a second time here would
        mean two policies that can disagree about one result.

        Parsing happens here rather than at the route, so a stored document
        that no longer satisfies the bounds is one vocabulary of failure —
        "this result cannot be read" — instead of an unhandled validation error
        surfacing as a 500.
        """

        execution = await self.get_for_run(
            user_id=user_id,
            run_id=run_id,
            execution_id=execution_id,
        )
        if self._preview_reader is None:
            raise ResultUnavailableError(
                "this deployment does not have result storage configured"
            )
        if execution.status is not ExecutionStatus.SUCCEEDED:
            # A reserved, running or failed execution has no published bundle.
            # Saying so is more useful than a download error from the store.
            raise ResultUnavailableError(
                "this execution has not published a result"
            )
        document = await self._preview_reader.read_preview(execution)
        try:
            preview = ResultPreview.model_validate(document)
        except ValidationError as error:
            raise ResultUnavailableError(
                "the stored preview does not satisfy the preview contract"
            ) from error
        return execution, preview


__all__ = ["ExecutionReadService"]
