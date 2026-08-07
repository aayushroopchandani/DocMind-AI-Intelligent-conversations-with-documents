from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from ..models import (
    AnalysisEventType,
    AnalysisRun,
    AnalysisRunStatus,
    CreateAnalysisRunRequest,
    DatasetVersionReference,
    RunIssueSummary,
    request_fingerprint,
)
from ..repositories.artifacts import (
    ArtifactNotFoundError,
    ArtifactRepositoryError,
    ArtifactStateConflictError,
)
from ..repositories.datasets import (
    DatasetCatalogConflictError,
    DatasetCatalogError,
)
from ..repositories.runs import (
    AnalysisRunIdempotencyConflictError,
    AnalysisRunLeaseConflictError,
    AnalysisRunNotFoundError,
    AnalysisRunStore,
    CreateRunResult,
    RunMutationResult,
)
from ..storage.base import (
    BlobConflictError,
    BlobIntegrityError,
    BlobNotFoundError,
    BlobStoreError,
    BlobStoreUnavailableError,
)
from ..storage.validation import ArtifactValidationError
from .artifacts import (
    ArtifactFinalizationPendingError,
    ArtifactServiceError,
    ArtifactUploadFailedError,
    ArtifactVersionInProgressError,
)
from .state_machine import AnalysisRunStateMachine
from .workbook_context import (
    WorkbookContextError,
    WorkbookContextService,
    WorkbookContextTooLargeError,
)
from ..integration.metadata import (
    phase7_model_versions,
    phase7_prompt_versions,
)


class AnalysisRunServiceError(RuntimeError):
    """Base error raised by the durable run application service."""


class AnalysisRuntimeUnavailableError(AnalysisRunServiceError):
    """A requested source adapter is not configured in this deployment."""


class InvalidRunCursorError(AnalysisRunServiceError):
    """The run-history cursor is malformed or incompatible."""


class InvalidRunResumeError(AnalysisRunServiceError):
    """A run cannot be resumed using the requested lifecycle operation."""


@dataclass(frozen=True, slots=True)
class AnalysisRunPage:
    items: tuple[AnalysisRun, ...]
    next_cursor: str | None


class AnalysisRunService:
    """Tenant-scoped application boundary used by HTTP and background workers."""

    def __init__(
        self,
        *,
        store: AnalysisRunStore,
        state_machine: AnalysisRunStateMachine,
        workbook_context: WorkbookContextService | None = None,
        run_deadline_seconds: int = 2 * 60 * 60,
        input_initialization_timeout_seconds: int = 15 * 60,
    ) -> None:
        if not 60 <= run_deadline_seconds <= 7 * 24 * 60 * 60:
            raise ValueError(
                "run_deadline_seconds must be between 60 and 604800"
            )
        if not (
            30
            <= input_initialization_timeout_seconds
            <= run_deadline_seconds
        ):
            raise ValueError(
                "input_initialization_timeout_seconds must be between "
                "30 and run_deadline_seconds"
            )
        self._store = store
        self._state_machine = state_machine
        self._workbook_context = workbook_context
        self._run_deadline = timedelta(seconds=run_deadline_seconds)
        self._input_initialization_timeout = timedelta(
            seconds=input_initialization_timeout_seconds
        )

    @property
    def event_store(self) -> AnalysisRunStore:
        return self._store

    async def create_run(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        request: CreateAnalysisRunRequest,
        trace_id: str | None = None,
    ) -> CreateRunResult:
        fingerprint = request_fingerprint(request)
        existing = await self._store.get_run_by_idempotency_key(
            user_id=user_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise AnalysisRunIdempotencyConflictError(
                    "idempotency key was already used for a different request"
                )
            events = await self._store.list_events(
                user_id=user_id,
                run_id=existing.run_id,
                after_sequence=0,
                limit=1,
            )
            if not events:
                raise AnalysisRunServiceError(
                    "the existing run has no durable creation event"
                )
            creation = CreateRunResult(
                run=existing,
                event=events[0],
                created=False,
            )
            return await self._complete_spreadsheet_initialization(
                creation=creation,
                user_id=user_id,
                request=request,
                trace_id=trace_id,
            )

        spreadsheet_pending = request.spreadsheet_context is not None
        artifact_version_ids: tuple[str, ...] = ()
        if (
            not spreadsheet_pending
            and request.active_artifact is not None
            and request.active_artifact.artifact_version_id is not None
        ):
            artifact_version_ids = (
                request.active_artifact.artifact_version_id,
            )
        active_artifact_id = (
            request.active_artifact.artifact_id
            if request.active_artifact is not None
            else None
        )

        chat_id = (
            request.pdf_context.chat_id
            if request.pdf_context is not None
            and request.pdf_context.chat_id is not None
            else request.workspace_id
        )
        now = datetime.now(timezone.utc)
        run = AnalysisRun(
            run_id=str(uuid4()),
            user_id=user_id,
            workspace_id=request.workspace_id,
            chat_id=chat_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            mode=request.mode,
            prompt=request.prompt,
            active_artifact_id=active_artifact_id,
            inputs_ready=not spreadsheet_pending,
            input_artifact_version_ids=artifact_version_ids,
            selected_document_ids=request.selected_document_ids,
            model_versions=phase7_model_versions(),
            prompt_versions=phase7_prompt_versions(),
            created_at=now,
            updated_at=now,
            expires_at=(
                now + self._input_initialization_timeout
                if spreadsheet_pending
                else now + self._run_deadline
            ),
        )
        creation = await self._state_machine.create_run(
            run=run,
            payload={
                "mode": run.mode.value,
                "source_count": (
                    len(run.selected_document_ids)
                    + int(spreadsheet_pending)
                ),
                "inputs_ready": run.inputs_ready,
                "events_url": f"/analysis/runs/{run.run_id}/events",
            },
            trace_id=trace_id,
        )
        return await self._complete_spreadsheet_initialization(
            creation=creation,
            user_id=user_id,
            request=request,
            trace_id=trace_id,
        )

    async def _complete_spreadsheet_initialization(
        self,
        *,
        creation: CreateRunResult,
        user_id: str,
        request: CreateAnalysisRunRequest,
        trace_id: str | None,
    ) -> CreateRunResult:
        """Resume the deterministic input sync for a newly created or replayed run."""

        context = request.spreadsheet_context
        if (
            context is None
            or creation.run.inputs_ready
            or creation.run.status != AnalysisRunStatus.CREATED
            or creation.run.cancellation_requested
        ):
            return creation
        deadline_result = (
            await self._state_machine.expire_abandoned_input_initialization(
                user_id=user_id,
                run_id=creation.run.run_id,
                trace_id=trace_id,
            )
        )
        if deadline_result is not None:
            return CreateRunResult(
                run=deadline_result.run,
                event=creation.event,
                created=creation.created,
            )
        if self._workbook_context is None:
            # The durable run intentionally remains non-claimable. A retry
            # after storage configuration is restored resumes this stage.
            raise AnalysisRuntimeUnavailableError(
                "spreadsheet artifact storage is not configured"
            )

        try:
            resolved = await self._workbook_context.resolve(
                user_id=user_id,
                workspace_id=request.workspace_id,
                context=context,
                active_artifact=request.active_artifact,
            )
        except Exception as exc:
            permanent_error = _permanent_input_error(exc)
            if permanent_error is None:
                # Provider, MongoDB, and ambiguous infrastructure failures are
                # deliberately resumable under the same idempotency key.
                raise
            terminal = (
                await self._state_machine.fail_input_initialization(
                    user_id=user_id,
                    run_id=creation.run.run_id,
                    error=permanent_error,
                    trace_id=trace_id,
                )
            )
            if terminal.run.status == AnalysisRunStatus.FAILED:
                # Preserve the existing HTTP 413/422 classification while the
                # run history and SSE stream retain the durable failure.
                raise
            # Cancellation or a concurrent successful initializer won the CAS.
            return CreateRunResult(
                run=terminal.run,
                event=creation.event,
                created=creation.created,
            )
        dataset_versions = tuple(
            DatasetVersionReference(
                dataset_id=handle.dataset_id,
                source_version=handle.source_version,
            )
            for handle in resolved.dataset_handles
        )
        initialized = await self._state_machine.complete_input_initialization(
            user_id=user_id,
            run_id=creation.run.run_id,
            active_artifact_id=resolved.dataset_handle.locator.artifact_id,
            artifact_version_ids=tuple(
                dict.fromkeys(
                    version_id
                    for version_id in (
                        resolved.source_artifact_version_id,
                        resolved.workbook_artifact_version_id,
                        *resolved.dataset_artifact_version_ids,
                    )
                    if version_id is not None
                )
            ),
            dataset_versions=dataset_versions,
            execution_expires_at=(
                datetime.now(timezone.utc) + self._run_deadline
            ),
            trace_id=trace_id,
        )
        return CreateRunResult(
            run=initialized.run,
            event=creation.event,
            created=creation.created,
        )

    async def get_run(self, *, user_id: str, run_id: str) -> AnalysisRun:
        run = await self._store.get_run(user_id=user_id, run_id=run_id)
        if run is None:
            raise AnalysisRunNotFoundError("analysis run not found")
        return run

    async def list_runs(
        self,
        *,
        user_id: str,
        workspace_id: str | None = None,
        status: AnalysisRunStatus | None = None,
        cursor: str | None = None,
        limit: int = 25,
    ) -> AnalysisRunPage:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        before_created_at, before_run_id = _decode_cursor(cursor)
        rows = await self._store.list_runs(
            user_id=user_id,
            workspace_id=workspace_id,
            status=status,
            before_created_at=before_created_at,
            before_run_id=before_run_id,
            limit=limit + 1,
        )
        items = rows[:limit]
        next_cursor = None
        if len(rows) > limit and items:
            last = items[-1]
            next_cursor = _encode_cursor(last.created_at, last.run_id)
        return AnalysisRunPage(items=items, next_cursor=next_cursor)

    async def cancel_run(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int | None = None,
        trace_id: str | None = None,
    ) -> RunMutationResult:
        requested = await self._state_machine.request_cancellation(
            user_id=user_id,
            run_id=run_id,
            expected_version=expected_version,
            payload={"requested_by": "user"},
            trace_id=trace_id,
        )
        if requested.run.status in {
            AnalysisRunStatus.SUCCEEDED,
            AnalysisRunStatus.FAILED,
            AnalysisRunStatus.CANCELLED,
            AnalysisRunStatus.EXPIRED,
        }:
            return requested
        try:
            return await self._state_machine.finalize_requested_cancellation(
                user_id=user_id,
                run_id=run_id,
                trace_id=trace_id,
            )
        except AnalysisRunLeaseConflictError:
            # The active worker owns the handoff and will stop after its
            # current graph milestone. A crashed worker is swept after expiry.
            return requested

    async def pause_run(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int | None = None,
        trace_id: str | None = None,
    ) -> RunMutationResult:
        requested = await self._state_machine.request_pause(
            user_id=user_id,
            run_id=run_id,
            expected_version=expected_version,
            trace_id=trace_id,
        )
        if (
            requested.run.status == AnalysisRunStatus.PAUSED
            or requested.run.status in {
                AnalysisRunStatus.SUCCEEDED,
                AnalysisRunStatus.FAILED,
                AnalysisRunStatus.CANCELLED,
                AnalysisRunStatus.EXPIRED,
            }
        ):
            return requested
        try:
            return await self._state_machine.finalize_requested_pause(
                user_id=user_id,
                run_id=run_id,
                trace_id=trace_id,
            )
        except AnalysisRunLeaseConflictError:
            # A live worker checkpoints after the current safe graph boundary.
            return requested

    async def resume_run(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int | None = None,
        trace_id: str | None = None,
    ) -> RunMutationResult:
        return await self._state_machine.resume_paused_run(
            user_id=user_id,
            run_id=run_id,
            execution_expires_at=(
                datetime.now(timezone.utc) + self._run_deadline
            ),
            expected_version=expected_version,
            trace_id=trace_id,
        )

    async def resume_as_new_run(
        self,
        *,
        user_id: str,
        source_run_id: str,
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> CreateRunResult:
        """Create a linked retry while preserving the source audit record."""

        source = await self.get_run(user_id=user_id, run_id=source_run_id)
        if source.status not in {
            AnalysisRunStatus.CANCELLED,
            AnalysisRunStatus.FAILED,
            AnalysisRunStatus.EXPIRED,
        }:
            raise InvalidRunResumeError(
                "only cancelled, failed, or expired runs can resume as new"
            )
        semantic_resume = {
            "source_run_id": source.run_id,
            "source_version": source.version,
            "checkpoint_id": source.checkpoint_id,
            "mode": source.mode.value,
            "prompt": source.prompt,
            "artifacts": list(source.input_artifact_version_ids),
            "datasets": [
                item.model_dump(mode="json")
                for item in source.input_dataset_versions
            ],
            "documents": list(source.selected_document_ids),
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                semantic_resume,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        existing = await self._store.get_run_by_idempotency_key(
            user_id=user_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise AnalysisRunIdempotencyConflictError(
                    "idempotency key was already used for a different request"
                )
            events = await self._store.list_events(
                user_id=user_id,
                run_id=existing.run_id,
                limit=1,
            )
            if not events:
                raise AnalysisRunServiceError(
                    "the resumed run has no durable creation event"
                )
            return CreateRunResult(
                run=existing,
                event=events[0],
                created=False,
            )

        now = datetime.now(timezone.utc)
        run = AnalysisRun(
            run_id=str(uuid4()),
            user_id=user_id,
            workspace_id=source.workspace_id,
            chat_id=source.chat_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            mode=source.mode,
            prompt=source.prompt,
            active_artifact_id=source.active_artifact_id,
            inputs_ready=True,
            input_artifact_version_ids=source.input_artifact_version_ids,
            input_dataset_versions=source.input_dataset_versions,
            selected_document_ids=source.selected_document_ids,
            parent_run_id=source.run_id,
            root_run_id=source.root_run_id or source.run_id,
            checkpoint_id=source.checkpoint_id,
            last_completed_step_id=source.last_completed_step_id,
            model_versions=phase7_model_versions(),
            prompt_versions=phase7_prompt_versions(),
            created_at=now,
            updated_at=now,
            expires_at=now + self._run_deadline,
        )
        return await self._state_machine.create_run(
            run=run,
            event_type=AnalysisEventType.RUN_RESUMED_AS_NEW,
            payload={
                "parent_run_id": source.run_id,
                "root_run_id": run.root_run_id,
                "resume_from_checkpoint_id": source.checkpoint_id,
                "events_url": f"/analysis/runs/{run.run_id}/events",
            },
            trace_id=trace_id,
        )


def _permanent_input_error(error: Exception) -> RunIssueSummary | None:
    """Map known input defects to bounded, provider-neutral public metadata."""

    chain = _exception_chain(error)
    if any(isinstance(item, WorkbookContextTooLargeError) for item in chain):
        return RunIssueSummary(
            code="input_context_too_large",
            message="The spreadsheet input exceeds the supported size limits.",
            retryable=False,
        )
    if any(isinstance(item, ArtifactValidationError) for item in chain):
        return RunIssueSummary(
            code="input_artifact_invalid",
            message="The spreadsheet artifact is invalid or unsupported.",
            retryable=False,
        )
    if any(
        isinstance(
            item,
            (
                ArtifactStateConflictError,
                DatasetCatalogConflictError,
                BlobConflictError,
                BlobIntegrityError,
            ),
        )
        for item in chain
    ):
        return RunIssueSummary(
            code="input_artifact_conflict",
            message="The spreadsheet artifact conflicts with immutable input metadata.",
            retryable=False,
        )
    if any(
        isinstance(
            item,
            (
                ArtifactNotFoundError,
                BlobNotFoundError,
                ArtifactUploadFailedError,
            ),
        )
        for item in chain
    ) or any(
        type(item) is ArtifactServiceError
        and "not found" in str(item).casefold()
        for item in chain
    ):
        return RunIssueSummary(
            code="input_artifact_unavailable",
            message="The referenced spreadsheet artifact is unavailable.",
            retryable=False,
        )

    # These failures can recover without changing the immutable request.
    if any(
        isinstance(
            item,
            (
                ArtifactFinalizationPendingError,
                ArtifactVersionInProgressError,
                BlobStoreUnavailableError,
                ArtifactRepositoryError,
                DatasetCatalogError,
                TimeoutError,
                ConnectionError,
            ),
        )
        for item in chain
    ) or any(
        isinstance(item, ArtifactServiceError)
        and "not ready" in str(item).casefold()
        for item in chain
    ):
        return None
    if any(
        isinstance(item, (BlobStoreError, ArtifactServiceError))
        for item in chain
    ):
        return None

    if any(isinstance(item, WorkbookContextError) for item in chain):
        return RunIssueSummary(
            code="input_context_invalid",
            message=(
                "The spreadsheet input is invalid or no longer matches "
                "the selected workbook."
            ),
            retryable=False,
        )
    if any(isinstance(item, ValueError) for item in chain):
        return RunIssueSummary(
            code="input_context_invalid",
            message="The spreadsheet input could not be validated.",
            retryable=False,
        )
    # Unknown failures are conservatively resumable and remain bounded by the
    # initialization deadline instead of being misclassified as user errors.
    return None


def _exception_chain(error: Exception) -> tuple[BaseException, ...]:
    output: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(output) < 16:
        output.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return tuple(output)


def _encode_cursor(created_at: datetime, run_id: str) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "created_at": created_at.astimezone(timezone.utc).isoformat(),
            "run_id": run_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(
    cursor: str | None,
) -> tuple[datetime | None, str | None]:
    if cursor is None:
        return None, None
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(cursor + padding)
        payload = json.loads(decoded)
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        if created_at.tzinfo is None:
            raise ValueError
        run_id = str(UUID(str(payload["run_id"])))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidRunCursorError("invalid analysis run cursor") from exc
    return created_at.astimezone(timezone.utc), run_id


__all__ = [
    "AnalysisRunPage",
    "AnalysisRunService",
    "AnalysisRunServiceError",
    "AnalysisRuntimeUnavailableError",
    "InvalidRunCursorError",
    "InvalidRunResumeError",
]
