from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import JsonValue

from scripts.data_analysis_agent.runtime.models.events import (
    AnalysisEventType,
)
from scripts.data_analysis_agent.runtime.models.runs import (
    TERMINAL_RUN_STATUSES,
    AnalysisRun,
    AnalysisRunOutcome,
    AnalysisRunPhase,
    AnalysisRunStatus,
    DatasetVersionReference,
    RunIssueSummary,
)
from scripts.data_analysis_agent.runtime.repositories.runs import (
    AnalysisRunConflictError,
    AnalysisRunLeaseConflictError,
    AnalysisRunNotFoundError,
    AnalysisRunStore,
    CreateRunResult,
    RunMutationResult,
)


class InvalidAnalysisRunTransition(ValueError):
    """A requested lifecycle change violates the run state machine."""


_ALLOWED_STATUS_TRANSITIONS: dict[
    AnalysisRunStatus,
    frozenset[AnalysisRunStatus],
] = {
    AnalysisRunStatus.CREATED: frozenset(
        {
            AnalysisRunStatus.ACTIVE,
            AnalysisRunStatus.PAUSED,
            AnalysisRunStatus.FAILED,
            AnalysisRunStatus.CANCELLED,
            AnalysisRunStatus.EXPIRED,
        }
    ),
    AnalysisRunStatus.ACTIVE: frozenset(
        {
            AnalysisRunStatus.ACTIVE,
            AnalysisRunStatus.WAITING,
            AnalysisRunStatus.PAUSED,
            AnalysisRunStatus.SUCCEEDED,
            AnalysisRunStatus.FAILED,
            AnalysisRunStatus.CANCELLED,
            AnalysisRunStatus.EXPIRED,
        }
    ),
    AnalysisRunStatus.WAITING: frozenset(
        {
            AnalysisRunStatus.ACTIVE,
            # The patch flow waits three times in a row — for context, for
            # approval, then while the browser applies. Each is a distinct
            # outcome at a non-decreasing phase, so the run stays waiting
            # rather than briefly pretending to be active in between.
            AnalysisRunStatus.WAITING,
            AnalysisRunStatus.PAUSED,
            AnalysisRunStatus.SUCCEEDED,
            AnalysisRunStatus.FAILED,
            AnalysisRunStatus.CANCELLED,
            AnalysisRunStatus.EXPIRED,
        }
    ),
    AnalysisRunStatus.PAUSED: frozenset(
        {
            AnalysisRunStatus.CREATED,
            AnalysisRunStatus.WAITING,
            AnalysisRunStatus.CANCELLED,
        }
    ),
    AnalysisRunStatus.SUCCEEDED: frozenset(),
    AnalysisRunStatus.FAILED: frozenset(),
    AnalysisRunStatus.CANCELLED: frozenset(),
    AnalysisRunStatus.EXPIRED: frozenset(),
}

_PHASE_RANK = {
    AnalysisRunPhase.CONTEXT_RESOLUTION: 0,
    AnalysisRunPhase.EVIDENCE_PREPARATION: 1,
    AnalysisRunPhase.REQUIREMENTS: 2,
    AnalysisRunPhase.NORMALIZATION: 3,
    AnalysisRunPhase.PLANNING: 4,
    AnalysisRunPhase.PLAN_VALIDATION: 5,
    AnalysisRunPhase.APPROVAL: 6,
    AnalysisRunPhase.EXECUTION: 7,
    AnalysisRunPhase.RESULT_VALIDATION: 8,
    AnalysisRunPhase.PROPOSAL: 9,
    AnalysisRunPhase.APPLICATION: 10,
    AnalysisRunPhase.COMPLETED: 11,
}

_STATUS_EVENT = {
    AnalysisRunStatus.FAILED: AnalysisEventType.RUN_FAILED,
    AnalysisRunStatus.CANCELLED: AnalysisEventType.RUN_CANCELLED,
    AnalysisRunStatus.EXPIRED: AnalysisEventType.RUN_EXPIRED,
}

_TERMINAL_OUTCOME = {
    AnalysisRunStatus.FAILED: AnalysisRunOutcome.FAILED,
    AnalysisRunStatus.CANCELLED: AnalysisRunOutcome.CANCELLED,
    AnalysisRunStatus.EXPIRED: AnalysisRunOutcome.EXPIRED,
}

# PLAN_APPROVED is intentionally absent: it completes the run when no native
# engine is installed, but keeps it waiting once approval feeds the execution
# queue. PLAN_READY is only ever emitted by the completing branch.
_TERMINAL_EVENT_TYPES = frozenset(
    {
        AnalysisEventType.RUN_COMPLETED,
        AnalysisEventType.RUN_FAILED,
        AnalysisEventType.RUN_CANCELLED,
        AnalysisEventType.RUN_EXPIRED,
        AnalysisEventType.PLAN_READY,
        AnalysisEventType.PLAN_REJECTED,
        AnalysisEventType.PATCH_REJECTED,
    }
)

_SUMMARY_UPDATE_FIELDS = frozenset(
    {
        "warnings_summary",
        "errors_summary",
        "model_versions",
        "prompt_versions",
        "component_versions",
        "token_usage",
        "token_usage_by_stage",
        "privacy_summary",
        "timings_ms",
        "final_artifact_ids",
        "final_dataset_ids",
        "current_plan_id",
        "current_plan_revision",
        "current_plan_hash",
        "plan_approval_status",
        "current_execution_id",
        "current_execution_key",
        "current_patch_id",
        "current_patch_revision",
        "current_patch_hash",
        "patch_approval_status",
        "applied_workbook_revision",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _lease_guard(
    *,
    worker_id: str,
    lease_attempt: int,
    current_time: datetime,
) -> dict[str, object]:
    return {
        "worker_id": worker_id,
        "lease_attempt": lease_attempt,
        "lease_expires_at": {"$gt": current_time},
    }


class AnalysisRunStateMachine:
    """Single domain entry point for durable run lifecycle mutations."""

    def __init__(
        self,
        store: AnalysisRunStore,
        *,
        clock: Callable[[], datetime] = _utc_now,
        maximum_lease_seconds: int = 3600,
    ) -> None:
        if maximum_lease_seconds < 1:
            raise ValueError("maximum_lease_seconds must be positive")
        self._store = store
        self._clock = clock
        self._maximum_lease_seconds = maximum_lease_seconds

    def _now(self) -> datetime:
        return _as_utc(self._clock())

    def _operation_time(self, current: AnalysisRun) -> datetime:
        """Return a monotonic run-local timestamp across application nodes.

        Wall clocks on otherwise healthy workers can differ slightly. Durable
        lifecycle timestamps must never move behind a timestamp already stored
        on the run, or Pydantic validation would reject an otherwise valid CAS
        mutation before it reaches MongoDB.
        """

        candidates = [
            self._now(),
            current.created_at,
            current.updated_at,
        ]
        if current.started_at is not None:
            candidates.append(current.started_at)
        if current.cancellation_requested_at is not None:
            candidates.append(current.cancellation_requested_at)
        if current.pause_requested_at is not None:
            candidates.append(current.pause_requested_at)
        if current.paused_at is not None:
            candidates.append(current.paused_at)
        return max(candidates)

    def _lease_expiry(self, *, seconds: int, current_time: datetime) -> datetime:
        if not 1 <= seconds <= self._maximum_lease_seconds:
            raise ValueError(
                f"lease_seconds must be between 1 and "
                f"{self._maximum_lease_seconds}"
            )
        return current_time + timedelta(seconds=seconds)

    async def create_run(
        self,
        *,
        run: AnalysisRun,
        event_type: AnalysisEventType = AnalysisEventType.RUN_CREATED,
        payload: Mapping[str, JsonValue] | None = None,
        trace_id: str | None = None,
    ) -> CreateRunResult:
        return await self._store.create_run(
            run=run,
            event_type=event_type,
            payload=payload,
            trace_id=trace_id,
        )

    async def require_run(self, *, user_id: str, run_id: str) -> AnalysisRun:
        run = await self._store.get_run(user_id=user_id, run_id=run_id)
        if run is None:
            raise AnalysisRunNotFoundError("analysis run not found")
        return run

    async def complete_input_initialization(
        self,
        *,
        user_id: str,
        run_id: str,
        active_artifact_id: str | None,
        artifact_version_ids: tuple[str, ...],
        dataset_versions: tuple[DatasetVersionReference, ...],
        execution_expires_at: datetime | None = None,
        trace_id: str | None = None,
    ) -> RunMutationResult:
        """Atomically publish externally synchronized spreadsheet inputs.

        Cloudinary uploads and dataset-catalog registration are deterministic
        and may safely be repeated before this commit. This transaction is the
        single visibility boundary: workers see neither partial references nor
        a run that is claimable without its immutable inputs.
        """

        if not dataset_versions:
            raise InvalidAnalysisRunTransition(
                "spreadsheet initialization requires a dataset version"
            )
        normalized_execution_expiry = (
            _as_utc(execution_expires_at)
            if execution_expires_at is not None
            else None
        )
        execution_deadline_required = normalized_execution_expiry is not None
        payload: dict[str, JsonValue] = {
            "stage": "input_initialization",
            "artifact_version_count": len(artifact_version_ids),
            "dataset_version_count": len(dataset_versions),
        }
        deduplication_key = f"inputs-ready:{run_id}"
        duplicate = await self._idempotent_event_result(
            user_id=user_id,
            run_id=run_id,
            deduplication_key=deduplication_key,
            event_type=AnalysisEventType.CONTEXT_RESOLVED,
            payload=payload,
            expected_status=AnalysisRunStatus.CREATED,
            expected_phase=AnalysisRunPhase.CONTEXT_RESOLUTION,
        )
        if duplicate is not None:
            self._validate_initialized_inputs(
                run=duplicate.run,
                active_artifact_id=active_artifact_id,
                artifact_version_ids=artifact_version_ids,
                dataset_versions=dataset_versions,
                execution_deadline_required=execution_deadline_required,
            )
            return duplicate

        current = await self.require_run(user_id=user_id, run_id=run_id)
        if current.inputs_ready:
            self._validate_initialized_inputs(
                run=current,
                active_artifact_id=active_artifact_id,
                artifact_version_ids=artifact_version_ids,
                dataset_versions=dataset_versions,
                execution_deadline_required=execution_deadline_required,
            )
            return RunMutationResult(run=current, event=None, changed=False)
        if current.status != AnalysisRunStatus.CREATED:
            raise InvalidAnalysisRunTransition(
                "only a created run can finish input initialization"
            )
        if current.cancellation_requested:
            raise InvalidAnalysisRunTransition(
                "cancelled execution cannot finish input initialization"
            )

        # Preserve lifecycle monotonicity across small application-node clock
        # skew; Mongo's stored timestamp remains the lower bound.
        now = self._operation_time(current)
        if current.expires_at is not None and current.expires_at <= now:
            raise InvalidAnalysisRunTransition(
                "input initialization deadline has elapsed"
            )
        if (
            normalized_execution_expiry is not None
            and normalized_execution_expiry <= now
        ):
            raise InvalidAnalysisRunTransition(
                "execution deadline must be in the future"
            )
        try:
            result = await self._store.mutate_with_event(
                user_id=user_id,
                run_id=run_id,
                expected_version=current.version,
                updates={
                    "active_artifact_id": active_artifact_id,
                    "input_artifact_version_ids": artifact_version_ids,
                    "input_dataset_versions": dataset_versions,
                    "inputs_ready": True,
                    # Replace the short pre-execution synchronization window
                    # with the run's complete queue/execution deadline.
                    "expires_at": normalized_execution_expiry,
                    "updated_at": now,
                },
                event_type=AnalysisEventType.CONTEXT_RESOLVED,
                payload=payload,
                deduplication_key=deduplication_key,
                trace_id=trace_id,
                additional_filter={
                    "status": AnalysisRunStatus.CREATED.value,
                    "inputs_ready": False,
                    "cancellation_requested": False,
                    "worker_id": None,
                    "$or": [
                        {"expires_at": None},
                        {"expires_at": {"$gt": now}},
                    ],
                },
            )
            self._validate_initialized_inputs(
                run=result.run,
                active_artifact_id=active_artifact_id,
                artifact_version_ids=artifact_version_ids,
                dataset_versions=dataset_versions,
                execution_deadline_required=execution_deadline_required,
            )
            return result
        except AnalysisRunConflictError:
            # Another identical initializer may have committed between our
            # read and CAS. Recover that durable result instead of making an
            # idempotent client retry fail spuriously.
            duplicate = await self._idempotent_event_result(
                user_id=user_id,
                run_id=run_id,
                deduplication_key=deduplication_key,
                event_type=AnalysisEventType.CONTEXT_RESOLVED,
                payload=payload,
                expected_status=AnalysisRunStatus.CREATED,
                expected_phase=AnalysisRunPhase.CONTEXT_RESOLUTION,
            )
            if duplicate is None:
                raise
            self._validate_initialized_inputs(
                run=duplicate.run,
                active_artifact_id=active_artifact_id,
                artifact_version_ids=artifact_version_ids,
                dataset_versions=dataset_versions,
                execution_deadline_required=execution_deadline_required,
            )
            return duplicate

    async def fail_input_initialization(
        self,
        *,
        user_id: str,
        run_id: str,
        error: RunIssueSummary,
        trace_id: str | None = None,
    ) -> RunMutationResult:
        """Fail an unusable immutable input, with cancellation taking priority."""

        if error.retryable:
            raise InvalidAnalysisRunTransition(
                "retryable input errors must remain resumable"
            )
        payload: dict[str, JsonValue] = {
            "stage": "input_initialization",
            "error": {
                "code": error.code,
                "message": error.message,
                "retryable": False,
            },
        }
        deduplication_key = f"input-failed:{run_id}"
        duplicate = await self._idempotent_event_result(
            user_id=user_id,
            run_id=run_id,
            deduplication_key=deduplication_key,
            event_type=AnalysisEventType.RUN_FAILED,
            payload=payload,
            expected_status=AnalysisRunStatus.FAILED,
            expected_phase=AnalysisRunPhase.COMPLETED,
            expected_outcome=AnalysisRunOutcome.FAILED,
        )
        if duplicate is not None:
            return duplicate

        current = await self.require_run(user_id=user_id, run_id=run_id)
        if current.status in TERMINAL_RUN_STATUSES:
            return RunMutationResult(run=current, event=None, changed=False)
        if current.cancellation_requested:
            return await self.finalize_requested_cancellation(
                user_id=user_id,
                run_id=run_id,
                trace_id=trace_id,
            )
        if current.inputs_ready:
            # A concurrent successful resolver won. Never convert ready input
            # into a failure based on another resolver's stale observation.
            return RunMutationResult(run=current, event=None, changed=False)
        if current.status != AnalysisRunStatus.CREATED:
            raise InvalidAnalysisRunTransition(
                "only pending input initialization can fail this way"
            )

        try:
            return await self.transition(
                user_id=user_id,
                run_id=run_id,
                target_status=AnalysisRunStatus.FAILED,
                target_phase=AnalysisRunPhase.COMPLETED,
                outcome=AnalysisRunOutcome.FAILED,
                event_type=AnalysisEventType.RUN_FAILED,
                payload=payload,
                expected_version=current.version,
                deduplication_key=deduplication_key,
                trace_id=trace_id,
                summary_updates={"errors_summary": (error,)},
            )
        except (
            AnalysisRunConflictError,
            InvalidAnalysisRunTransition,
        ):
            duplicate = await self._idempotent_event_result(
                user_id=user_id,
                run_id=run_id,
                deduplication_key=deduplication_key,
                event_type=AnalysisEventType.RUN_FAILED,
                payload=payload,
                expected_status=AnalysisRunStatus.FAILED,
                expected_phase=AnalysisRunPhase.COMPLETED,
                expected_outcome=AnalysisRunOutcome.FAILED,
            )
            if duplicate is not None:
                return duplicate
            latest = await self.require_run(user_id=user_id, run_id=run_id)
            if latest.cancellation_requested:
                return await self.finalize_requested_cancellation(
                    user_id=user_id,
                    run_id=run_id,
                    trace_id=trace_id,
                )
            if latest.status in TERMINAL_RUN_STATUSES or latest.inputs_ready:
                return RunMutationResult(
                    run=latest,
                    event=None,
                    changed=False,
                )
            raise

    async def expire_abandoned_input_initialization(
        self,
        *,
        user_id: str,
        run_id: str,
        trace_id: str | None = None,
    ) -> RunMutationResult | None:
        """Apply the pending-input deadline before an idempotent retry."""

        current = await self.require_run(user_id=user_id, run_id=run_id)
        if current.status in TERMINAL_RUN_STATUSES or current.inputs_ready:
            return None
        if current.status != AnalysisRunStatus.CREATED:
            return None
        if current.cancellation_requested:
            return await self.finalize_requested_cancellation(
                user_id=user_id,
                run_id=run_id,
                trace_id=trace_id,
            )
        if current.expires_at is None or current.expires_at > self._now():
            return None
        try:
            return await self.expire_run(
                user_id=user_id,
                run_id=run_id,
                trace_id=trace_id,
            )
        except (
            AnalysisRunConflictError,
            InvalidAnalysisRunTransition,
        ):
            latest = await self.require_run(user_id=user_id, run_id=run_id)
            if latest.cancellation_requested:
                return await self.finalize_requested_cancellation(
                    user_id=user_id,
                    run_id=run_id,
                    trace_id=trace_id,
                )
            if latest.status in TERMINAL_RUN_STATUSES or latest.inputs_ready:
                return RunMutationResult(
                    run=latest,
                    event=None,
                    changed=False,
                )
            raise

    @staticmethod
    def _validate_initialized_inputs(
        *,
        run: AnalysisRun,
        active_artifact_id: str | None,
        artifact_version_ids: tuple[str, ...],
        dataset_versions: tuple[DatasetVersionReference, ...],
        execution_deadline_required: bool,
    ) -> None:
        deadline_matches = (
            run.expires_at is not None
            if execution_deadline_required
            else run.expires_at is None
        )
        if (
            not run.inputs_ready
            or run.active_artifact_id != active_artifact_id
            or run.input_artifact_version_ids != artifact_version_ids
            or run.input_dataset_versions != dataset_versions
            or not deadline_matches
        ):
            raise InvalidAnalysisRunTransition(
                "input initialization replay does not match the durable inputs"
            )

    async def _idempotent_event_result(
        self,
        *,
        user_id: str,
        run_id: str,
        deduplication_key: str | None,
        event_type: AnalysisEventType,
        payload: Mapping[str, JsonValue] | None = None,
        expected_status: AnalysisRunStatus | None = None,
        expected_phase: AnalysisRunPhase | None = None,
        expected_outcome: AnalysisRunOutcome | None = None,
    ) -> RunMutationResult | None:
        if not deduplication_key:
            return None
        event = await self._store.get_event_by_deduplication_key(
            user_id=user_id,
            run_id=run_id,
            deduplication_key=deduplication_key,
        )
        if event is None:
            return None
        if (
            event.event_type != event_type
            or event.payload != dict(payload or {})
            or (
                expected_status is not None
                and event.status != expected_status
            )
            or (
                expected_phase is not None
                and event.phase != expected_phase
            )
        ):
            raise InvalidAnalysisRunTransition(
                "deduplication key belongs to a different command"
            )
        run = await self.require_run(user_id=user_id, run_id=run_id)
        if (
            expected_outcome is not None
            and run.outcome != expected_outcome
        ):
            raise InvalidAnalysisRunTransition(
                "deduplication key belongs to a different outcome"
            )
        return RunMutationResult(run=run, event=event, changed=False)

    @staticmethod
    def _validate_phase_progress(
        current: AnalysisRunPhase,
        target: AnalysisRunPhase,
    ) -> None:
        if _PHASE_RANK[target] < _PHASE_RANK[current]:
            raise InvalidAnalysisRunTransition(
                f"run phase cannot move backward from {current.value} "
                f"to {target.value}"
            )

    @staticmethod
    def _validate_target(
        *,
        current: AnalysisRun,
        target_status: AnalysisRunStatus,
        target_phase: AnalysisRunPhase,
        outcome: AnalysisRunOutcome | None,
        event_type: AnalysisEventType,
    ) -> None:
        allowed = _ALLOWED_STATUS_TRANSITIONS[current.status]
        if target_status not in allowed:
            raise InvalidAnalysisRunTransition(
                f"cannot transition {current.status.value} to "
                f"{target_status.value}"
            )
        if (
            event_type in _TERMINAL_EVENT_TYPES
            and target_status not in TERMINAL_RUN_STATUSES
        ):
            raise InvalidAnalysisRunTransition(
                "terminal events require a terminal run status"
            )

        AnalysisRunStateMachine._validate_phase_progress(
            current.phase,
            target_phase,
        )

        terminal = target_status in TERMINAL_RUN_STATUSES
        if terminal != (target_phase == AnalysisRunPhase.COMPLETED):
            raise InvalidAnalysisRunTransition(
                "terminal status and completed phase must be set together"
            )

        expected_event = _STATUS_EVENT.get(target_status)
        if target_status != current.status and expected_event is not None:
            if event_type != expected_event:
                raise InvalidAnalysisRunTransition(
                    f"{target_status.value} transitions require "
                    f"{expected_event.value}"
                )

        if target_status == AnalysisRunStatus.WAITING:
            expected_wait_event = {
                AnalysisRunOutcome.CLARIFICATION_REQUIRED: (
                    AnalysisEventType.CLARIFICATION_REQUIRED
                ),
                AnalysisRunOutcome.PLAN_READY: (
                    AnalysisEventType.PLAN_APPROVAL_REQUIRED
                ),
                # Phase 9.11/9.12 park the run three more times: once for the
                # live workbook context, once for patch approval, and once
                # while the browser applies the approved patch.
                AnalysisRunOutcome.PATCH_CONTEXT_REQUIRED: (
                    AnalysisEventType.PATCH_CONTEXT_REQUIRED
                ),
                AnalysisRunOutcome.PATCH_READY: (
                    AnalysisEventType.PATCH_APPROVAL_REQUIRED
                ),
                AnalysisRunOutcome.AWAITING_APPLICATION: (
                    AnalysisEventType.PATCH_APPROVED
                ),
            }.get(outcome)
            execution_events = {
                AnalysisEventType.EXECUTION_QUEUED,
                AnalysisEventType.PLAN_APPROVED,
            }
            if (
                outcome == AnalysisRunOutcome.QUEUED_FOR_EXECUTION
                and event_type in execution_events
            ):
                expected_wait_event = event_type
            if expected_wait_event is None or event_type != expected_wait_event:
                raise InvalidAnalysisRunTransition(
                    "waiting runs must request clarification, approval, or execution"
                )
        elif target_status == AnalysisRunStatus.SUCCEEDED:
            if outcome not in {
                AnalysisRunOutcome.DATASETS_PREPARED,
                AnalysisRunOutcome.UNANSWERABLE,
                AnalysisRunOutcome.PLAN_READY,
                AnalysisRunOutcome.REJECTED,
                AnalysisRunOutcome.COMPLETED,
            }:
                raise InvalidAnalysisRunTransition(
                    "succeeded runs need a successful business outcome"
                )
            allowed_events = {
                AnalysisRunOutcome.DATASETS_PREPARED: {
                    AnalysisEventType.RUN_COMPLETED,
                },
                AnalysisRunOutcome.UNANSWERABLE: {
                    AnalysisEventType.RUN_COMPLETED,
                },
                # The plan is the deliverable while no native engine exists:
                # planning emits PLAN_READY, approval emits PLAN_APPROVED.
                AnalysisRunOutcome.PLAN_READY: {
                    AnalysisEventType.PLAN_READY,
                    AnalysisEventType.PLAN_APPROVED,
                },
                AnalysisRunOutcome.REJECTED: {
                    AnalysisEventType.PLAN_REJECTED,
                    AnalysisEventType.PATCH_REJECTED,
                },
                AnalysisRunOutcome.COMPLETED: {
                    AnalysisEventType.RUN_COMPLETED,
                },
            }[outcome]
            if event_type not in allowed_events:
                raise InvalidAnalysisRunTransition(
                    "succeeded transition uses an incompatible event"
                )
        elif target_status in _TERMINAL_OUTCOME:
            if outcome != _TERMINAL_OUTCOME[target_status]:
                raise InvalidAnalysisRunTransition(
                    f"{target_status.value} runs need "
                    f"{_TERMINAL_OUTCOME[target_status].value}"
                )
        elif target_status != AnalysisRunStatus.WAITING and outcome is not None:
            raise InvalidAnalysisRunTransition(
                "non-terminal active runs cannot have an outcome"
            )

    async def transition(
        self,
        *,
        user_id: str,
        run_id: str,
        target_status: AnalysisRunStatus,
        target_phase: AnalysisRunPhase,
        outcome: AnalysisRunOutcome | None,
        event_type: AnalysisEventType,
        payload: Mapping[str, JsonValue] | None = None,
        expected_version: int | None = None,
        deduplication_key: str | None = None,
        trace_id: str | None = None,
        worker_id: str | None = None,
        lease_attempt: int | None = None,
        summary_updates: Mapping[str, object] | None = None,
    ) -> RunMutationResult:
        duplicate = await self._idempotent_event_result(
            user_id=user_id,
            run_id=run_id,
            deduplication_key=deduplication_key,
            event_type=event_type,
            payload=payload,
            expected_status=target_status,
            expected_phase=target_phase,
            expected_outcome=outcome,
        )
        if duplicate is not None:
            return duplicate

        current = await self.require_run(user_id=user_id, run_id=run_id)
        if expected_version is not None and current.version != expected_version:
            raise AnalysisRunConflictError(
                f"stale run version: expected {expected_version}, "
                f"found {current.version}"
            )
        self._validate_target(
            current=current,
            target_status=target_status,
            target_phase=target_phase,
            outcome=outcome,
            event_type=event_type,
        )
        if (
            current.cancellation_requested
            and target_status != AnalysisRunStatus.CANCELLED
        ):
            raise InvalidAnalysisRunTransition(
                "a cancellation-requested run must transition to cancelled"
            )
        if current.pause_requested and target_status != AnalysisRunStatus.PAUSED:
            raise InvalidAnalysisRunTransition(
                "a pause-requested run must checkpoint before progressing"
            )

        now = self._operation_time(current)
        additional_filter: Mapping[str, object] | None = None
        if current.worker_id is not None:
            if (
                worker_id != current.worker_id
                or lease_attempt != current.lease_attempt
            ):
                raise AnalysisRunLeaseConflictError(
                    "the active execution lease is owned by another worker"
                )
            additional_filter = _lease_guard(
                worker_id=current.worker_id,
                lease_attempt=current.lease_attempt,
                current_time=now,
            )

        updates: dict[str, object] = {
            "status": target_status,
            "phase": target_phase,
            "outcome": outcome,
            "updated_at": now,
        }
        if summary_updates:
            unsupported = set(summary_updates).difference(_SUMMARY_UPDATE_FIELDS)
            if unsupported:
                raise InvalidAnalysisRunTransition(
                    "unsupported run summary fields: "
                    + ", ".join(sorted(unsupported))
                )
            updates.update(summary_updates)
        if target_status == AnalysisRunStatus.ACTIVE and current.started_at is None:
            updates["started_at"] = now
        if target_status in TERMINAL_RUN_STATUSES:
            updates.update(
                {
                    "completed_at": now,
                    "worker_id": None,
                    "lease_expires_at": None,
                }
            )
        elif target_status == AnalysisRunStatus.WAITING:
            updates.update(
                {
                    "worker_id": None,
                    "lease_expires_at": None,
                }
            )
        elif current.status == AnalysisRunStatus.WAITING:
            # Clarification has been supplied and the business outcome is no
            # longer terminal/waiting.
            updates["outcome"] = None

        return await self._store.mutate_with_event(
            user_id=user_id,
            run_id=run_id,
            expected_version=current.version,
            updates=updates,
            event_type=event_type,
            payload=payload,
            deduplication_key=deduplication_key,
            trace_id=trace_id,
            additional_filter=additional_filter,
        )

    async def record_event(
        self,
        *,
        user_id: str,
        run_id: str,
        event_type: AnalysisEventType,
        payload: Mapping[str, JsonValue] | None = None,
        phase: AnalysisRunPhase | None = None,
        expected_version: int | None = None,
        deduplication_key: str | None = None,
        trace_id: str | None = None,
        worker_id: str | None = None,
        lease_attempt: int | None = None,
    ) -> RunMutationResult:
        if event_type in _TERMINAL_EVENT_TYPES:
            raise InvalidAnalysisRunTransition(
                "terminal events must be emitted by a lifecycle transition"
            )
        duplicate = await self._idempotent_event_result(
            user_id=user_id,
            run_id=run_id,
            deduplication_key=deduplication_key,
            event_type=event_type,
            payload=payload,
            expected_phase=phase,
        )
        if duplicate is not None:
            return duplicate

        current = await self.require_run(user_id=user_id, run_id=run_id)
        if current.status in TERMINAL_RUN_STATUSES:
            raise InvalidAnalysisRunTransition(
                "terminal runs cannot emit progress events"
            )
        if current.cancellation_requested:
            raise InvalidAnalysisRunTransition(
                "cancellation-requested runs cannot emit new progress events"
            )
        if current.pause_requested or current.status == AnalysisRunStatus.PAUSED:
            raise InvalidAnalysisRunTransition(
                "paused runs cannot emit new progress events"
            )
        if expected_version is not None and expected_version != current.version:
            raise AnalysisRunConflictError(
                f"stale run version: expected {expected_version}, "
                f"found {current.version}"
            )

        target_phase = phase or current.phase
        self._validate_phase_progress(current.phase, target_phase)
        now = self._operation_time(current)
        additional_filter: Mapping[str, object] | None = None
        if current.worker_id is not None:
            if (
                worker_id != current.worker_id
                or lease_attempt != current.lease_attempt
            ):
                raise AnalysisRunLeaseConflictError(
                    "the active execution lease is owned by another worker"
                )
            additional_filter = _lease_guard(
                worker_id=current.worker_id,
                lease_attempt=current.lease_attempt,
                current_time=now,
            )

        return await self._store.mutate_with_event(
            user_id=user_id,
            run_id=run_id,
            expected_version=current.version,
            updates={"phase": target_phase, "updated_at": now},
            event_type=event_type,
            payload=payload,
            deduplication_key=deduplication_key,
            trace_id=trace_id,
            additional_filter=additional_filter,
        )

    async def request_cancellation(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int | None = None,
        payload: Mapping[str, JsonValue] | None = None,
        trace_id: str | None = None,
    ) -> RunMutationResult:
        deduplication_key = f"cancel-request:{run_id}"
        duplicate = await self._idempotent_event_result(
            user_id=user_id,
            run_id=run_id,
            deduplication_key=deduplication_key,
            event_type=AnalysisEventType.CANCELLATION_REQUESTED,
            payload=payload,
        )
        if duplicate is not None:
            return duplicate

        current = await self.require_run(user_id=user_id, run_id=run_id)
        if current.status in TERMINAL_RUN_STATUSES:
            return RunMutationResult(run=current, event=None, changed=False)
        if current.cancellation_requested:
            return RunMutationResult(run=current, event=None, changed=False)
        if expected_version is not None and expected_version != current.version:
            raise AnalysisRunConflictError(
                f"stale run version: expected {expected_version}, "
                f"found {current.version}"
            )

        now = self._operation_time(current)
        try:
            return await self._store.mutate_with_event(
                user_id=user_id,
                run_id=run_id,
                expected_version=current.version,
                updates={
                    "cancellation_requested": True,
                    "cancellation_requested_at": now,
                    "pause_requested": False,
                    "pause_requested_at": None,
                    "updated_at": now,
                },
                event_type=AnalysisEventType.CANCELLATION_REQUESTED,
                payload=payload,
                deduplication_key=deduplication_key,
                trace_id=trace_id,
                additional_filter={
                    "cancellation_requested": False,
                    "status": {"$nin": [item.value for item in TERMINAL_RUN_STATUSES]},
                },
            )
        except AnalysisRunConflictError:
            # Resolve cancellation/completion races deterministically.
            latest = await self.require_run(user_id=user_id, run_id=run_id)
            if (
                latest.cancellation_requested
                or latest.status in TERMINAL_RUN_STATUSES
            ):
                event = await self._store.get_event_by_deduplication_key(
                    user_id=user_id,
                    run_id=run_id,
                    deduplication_key=deduplication_key,
                )
                return RunMutationResult(
                    run=latest,
                    event=event,
                    changed=False,
                )
            raise

    async def request_pause(
        self,
        *,
        user_id: str,
        run_id: str,
        expected_version: int | None = None,
        trace_id: str | None = None,
    ) -> RunMutationResult:
        """Persist a cooperative pause request without stealing a live lease."""

        current = await self.require_run(user_id=user_id, run_id=run_id)
        if current.status in TERMINAL_RUN_STATUSES:
            return RunMutationResult(run=current, event=None, changed=False)
        if current.status == AnalysisRunStatus.PAUSED or current.pause_requested:
            return RunMutationResult(run=current, event=None, changed=False)
        if current.status not in {
            AnalysisRunStatus.CREATED,
            AnalysisRunStatus.ACTIVE,
        }:
            raise InvalidAnalysisRunTransition(
                "only queued or executing runs can be paused"
            )
        if current.cancellation_requested:
            raise InvalidAnalysisRunTransition(
                "a cancellation-requested run cannot be paused"
            )
        if expected_version is not None and current.version != expected_version:
            raise AnalysisRunConflictError(
                f"stale run version: expected {expected_version}, "
                f"found {current.version}"
            )

        now = self._operation_time(current)
        cycle = current.resume_count
        return await self._store.mutate_with_event(
            user_id=user_id,
            run_id=run_id,
            expected_version=current.version,
            updates={
                "pause_requested": True,
                "pause_requested_at": now,
                "updated_at": now,
            },
            event_type=AnalysisEventType.PAUSE_REQUESTED,
            payload={"checkpoint_boundary": "next_safe_boundary"},
            deduplication_key=f"pause-request:{run_id}:{cycle}",
            trace_id=trace_id,
            additional_filter={
                "pause_requested": {"$ne": True},
                "cancellation_requested": False,
                "status": {
                    "$in": [
                        AnalysisRunStatus.CREATED.value,
                        AnalysisRunStatus.ACTIVE.value,
                    ]
                },
            },
        )

    async def finalize_requested_pause(
        self,
        *,
        user_id: str,
        run_id: str,
        worker_id: str | None = None,
        lease_attempt: int | None = None,
        last_completed_step_id: str | None = None,
        trace_id: str | None = None,
    ) -> RunMutationResult:
        """Checkpoint a pause at a safe boundary and release the lease."""

        current = await self.require_run(user_id=user_id, run_id=run_id)
        if current.status == AnalysisRunStatus.PAUSED:
            return RunMutationResult(run=current, event=None, changed=False)
        if current.status in TERMINAL_RUN_STATUSES:
            return RunMutationResult(run=current, event=None, changed=False)
        if current.cancellation_requested:
            raise InvalidAnalysisRunTransition(
                "cancellation takes priority over a pending pause"
            )
        if not current.pause_requested:
            raise InvalidAnalysisRunTransition(
                "run does not have a pending pause request"
            )

        now = self._operation_time(current)
        additional_filter: dict[str, object] = {
            "pause_requested": True,
            "cancellation_requested": False,
        }
        if current.worker_id is not None:
            if (
                current.worker_id != worker_id
                or current.lease_attempt != lease_attempt
            ):
                if (
                    current.lease_expires_at is not None
                    and current.lease_expires_at > now
                ):
                    raise AnalysisRunLeaseConflictError(
                        "the active worker still owns the pause handoff"
                    )
                additional_filter["$or"] = [
                    {"lease_expires_at": None},
                    {"lease_expires_at": {"$lte": now}},
                ]
            else:
                additional_filter.update(
                    _lease_guard(
                        worker_id=current.worker_id,
                        lease_attempt=current.lease_attempt,
                        current_time=now,
                    )
                )

        checkpoint_id = str(uuid4())
        return await self._store.mutate_with_event(
            user_id=user_id,
            run_id=run_id,
            expected_version=current.version,
            updates={
                "status": AnalysisRunStatus.PAUSED,
                "outcome": None,
                "pause_requested": False,
                "pause_requested_at": None,
                "paused_at": now,
                "checkpoint_id": checkpoint_id,
                "last_completed_step_id": (
                    last_completed_step_id or current.last_completed_step_id
                ),
                "paused_from_status": current.status,
                "paused_from_phase": current.phase,
                "paused_from_outcome": current.outcome,
                "worker_id": None,
                "lease_expires_at": None,
                # A paused run does not age out while it consumes no worker.
                "expires_at": None,
                "updated_at": now,
            },
            event_type=AnalysisEventType.RUN_PAUSED,
            payload={
                "checkpoint_id": checkpoint_id,
                "last_completed_step_id": last_completed_step_id,
            },
            deduplication_key=f"paused:{run_id}:{current.resume_count}",
            trace_id=trace_id,
            additional_filter=additional_filter,
        )

    async def resume_paused_run(
        self,
        *,
        user_id: str,
        run_id: str,
        execution_expires_at: datetime | None,
        expected_version: int | None = None,
        trace_id: str | None = None,
    ) -> RunMutationResult:
        """Requeue the same paused run from its latest durable checkpoint."""

        current = await self.require_run(user_id=user_id, run_id=run_id)
        if current.status != AnalysisRunStatus.PAUSED:
            raise InvalidAnalysisRunTransition("only a paused run can be resumed")
        if expected_version is not None and current.version != expected_version:
            raise AnalysisRunConflictError(
                f"stale run version: expected {expected_version}, "
                f"found {current.version}"
            )
        now = self._operation_time(current)
        normalized_expiry = (
            _as_utc(execution_expires_at)
            if execution_expires_at is not None
            else None
        )
        if normalized_expiry is not None and normalized_expiry <= now:
            raise InvalidAnalysisRunTransition(
                "resumed execution deadline must be in the future"
            )
        restore_wait = current.paused_from_status == AnalysisRunStatus.WAITING
        target_status = (
            AnalysisRunStatus.WAITING
            if restore_wait
            else AnalysisRunStatus.CREATED
        )
        target_outcome = current.paused_from_outcome if restore_wait else None
        target_phase = current.paused_from_phase or current.phase
        resume_count = current.resume_count + 1
        return await self._store.mutate_with_event(
            user_id=user_id,
            run_id=run_id,
            expected_version=current.version,
            updates={
                "status": target_status,
                "phase": target_phase,
                "outcome": target_outcome,
                "paused_at": None,
                "paused_from_status": None,
                "paused_from_phase": None,
                "paused_from_outcome": None,
                "resume_count": resume_count,
                "expires_at": normalized_expiry,
                "updated_at": now,
            },
            event_type=AnalysisEventType.RUN_RESUMED,
            payload={
                "checkpoint_id": current.checkpoint_id,
                "resume_count": resume_count,
                "requeued": not restore_wait,
            },
            deduplication_key=f"resumed:{run_id}:{resume_count}",
            trace_id=trace_id,
            additional_filter={
                "status": AnalysisRunStatus.PAUSED.value,
                "pause_requested": False,
                "cancellation_requested": False,
            },
        )

    async def claim_execution(
        self,
        *,
        user_id: str,
        run_id: str,
        worker_id: str,
        lease_seconds: int,
        expected_version: int | None = None,
        trace_id: str | None = None,
    ) -> RunMutationResult:
        current = await self.require_run(user_id=user_id, run_id=run_id)
        if expected_version is not None and expected_version != current.version:
            raise AnalysisRunConflictError(
                f"stale run version: expected {expected_version}, "
                f"found {current.version}"
            )
        if current.status not in {
            AnalysisRunStatus.CREATED,
            AnalysisRunStatus.ACTIVE,
        }:
            raise AnalysisRunLeaseConflictError(
                f"{current.status.value} runs cannot be claimed"
            )
        if not current.inputs_ready:
            raise AnalysisRunLeaseConflictError(
                "run inputs are not ready for execution"
            )
        if current.cancellation_requested:
            raise AnalysisRunLeaseConflictError(
                "cancelled execution cannot be claimed"
            )
        if current.pause_requested:
            raise AnalysisRunLeaseConflictError(
                "paused execution cannot be claimed"
            )

        now = self._operation_time(current)
        if current.expires_at is not None and current.expires_at <= now:
            raise AnalysisRunLeaseConflictError("expired run cannot be claimed")
        if (
            current.worker_id is not None
            and current.lease_expires_at is not None
            and current.lease_expires_at > now
        ):
            if current.worker_id == worker_id:
                return RunMutationResult(run=current, event=None, changed=False)
            raise AnalysisRunLeaseConflictError(
                "execution lease is currently owned by another worker"
            )

        lease_expires_at = self._lease_expiry(
            seconds=lease_seconds,
            current_time=now,
        )
        if (
            current.expires_at is not None
            and current.expires_at < lease_expires_at
        ):
            # A worker lease must never outlive the run itself; this makes the
            # hard run deadline immediately sweepable without stealing a
            # still-valid lease.
            lease_expires_at = current.expires_at
        next_attempt = current.lease_attempt + 1
        recovered = current.status == AnalysisRunStatus.ACTIVE or (
            current.lease_attempt > 0
        )
        event_type = (
            AnalysisEventType.RUN_RECOVERED
            if recovered
            else AnalysisEventType.RUN_STARTED
        )
        deduplication_key = f"lease-claim:{run_id}:{next_attempt}"
        updates: dict[str, object] = {
            "status": AnalysisRunStatus.ACTIVE,
            "worker_id": worker_id,
            "lease_expires_at": lease_expires_at,
            "lease_attempt": next_attempt,
            "updated_at": now,
        }
        if current.started_at is None:
            updates["started_at"] = now

        return await self._store.mutate_with_event(
            user_id=user_id,
            run_id=run_id,
            expected_version=current.version,
            updates=updates,
            event_type=event_type,
            payload={
                "attempt": next_attempt,
                "recovered": recovered,
            },
            deduplication_key=deduplication_key,
            trace_id=trace_id,
            additional_filter={
                "cancellation_requested": False,
                "pause_requested": {"$ne": True},
                "inputs_ready": {"$ne": False},
                "status": {
                    "$in": [
                        AnalysisRunStatus.CREATED.value,
                        AnalysisRunStatus.ACTIVE.value,
                    ]
                },
                "$or": [
                    {"worker_id": None},
                    {"lease_expires_at": None},
                    {"lease_expires_at": {"$lte": now}},
                ],
            },
        )

    async def renew_execution_lease(
        self,
        *,
        user_id: str,
        run_id: str,
        worker_id: str,
        lease_attempt: int,
        lease_seconds: int,
    ) -> AnalysisRun:
        current = await self.require_run(user_id=user_id, run_id=run_id)
        now = self._operation_time(current)
        if current.expires_at is not None and current.expires_at <= now:
            raise AnalysisRunLeaseConflictError(
                "expired run cannot renew its execution lease"
            )
        lease_expires_at = self._lease_expiry(
            seconds=lease_seconds,
            current_time=now,
        )
        if (
            current.expires_at is not None
            and current.expires_at < lease_expires_at
        ):
            lease_expires_at = current.expires_at
        return await self._store.renew_execution_lease(
            user_id=user_id,
            run_id=run_id,
            worker_id=worker_id,
            lease_attempt=lease_attempt,
            current_time=now,
            lease_expires_at=lease_expires_at,
        )

    async def release_execution_lease(
        self,
        *,
        user_id: str,
        run_id: str,
        worker_id: str,
        lease_attempt: int,
    ) -> AnalysisRun:
        return await self._store.release_execution_lease(
            user_id=user_id,
            run_id=run_id,
            worker_id=worker_id,
            lease_attempt=lease_attempt,
        )

    async def list_recoverable_runs(
        self,
        *,
        limit: int = 100,
    ) -> tuple[AnalysisRun, ...]:
        return await self._store.list_recoverable_runs(
            current_time=self._now(),
            limit=limit,
        )

    async def expire_run(
        self,
        *,
        user_id: str,
        run_id: str,
        trace_id: str | None = None,
    ) -> RunMutationResult:
        """Atomically terminalize one elapsed run and append RUN_EXPIRED."""

        payload: dict[str, JsonValue] = {"reason": "deadline_elapsed"}
        deduplication_key = f"expired:{run_id}"
        duplicate = await self._idempotent_event_result(
            user_id=user_id,
            run_id=run_id,
            deduplication_key=deduplication_key,
            event_type=AnalysisEventType.RUN_EXPIRED,
            payload=payload,
            expected_status=AnalysisRunStatus.EXPIRED,
            expected_phase=AnalysisRunPhase.COMPLETED,
            expected_outcome=AnalysisRunOutcome.EXPIRED,
        )
        if duplicate is not None:
            return duplicate

        current = await self.require_run(user_id=user_id, run_id=run_id)
        if current.status in TERMINAL_RUN_STATUSES:
            return RunMutationResult(run=current, event=None, changed=False)
        if current.status == AnalysisRunStatus.PAUSED:
            raise InvalidAnalysisRunTransition("paused runs do not expire")

        now = self._now()
        if current.expires_at is None or current.expires_at > now:
            raise InvalidAnalysisRunTransition(
                "run deadline has not elapsed"
            )
        if current.cancellation_requested:
            raise InvalidAnalysisRunTransition(
                "a cancellation-requested run must be cancelled"
            )
        if (
            current.worker_id is not None
            and current.lease_expires_at is not None
            and current.lease_expires_at > now
        ):
            raise AnalysisRunLeaseConflictError(
                "the active worker still owns the run"
            )

        updated_at = self._operation_time(current)
        try:
            return await self._store.mutate_with_event(
                user_id=user_id,
                run_id=run_id,
                expected_version=current.version,
                updates={
                    "status": AnalysisRunStatus.EXPIRED,
                    "phase": AnalysisRunPhase.COMPLETED,
                    "outcome": AnalysisRunOutcome.EXPIRED,
                    "updated_at": updated_at,
                    "completed_at": updated_at,
                    "worker_id": None,
                    "lease_expires_at": None,
                    "pause_requested": False,
                    "pause_requested_at": None,
                    "paused_at": None,
                    "paused_from_status": None,
                    "paused_from_phase": None,
                    "paused_from_outcome": None,
                },
                event_type=AnalysisEventType.RUN_EXPIRED,
                payload=payload,
                deduplication_key=deduplication_key,
                trace_id=trace_id,
                additional_filter={
                    "cancellation_requested": False,
                    "status": {
                        "$in": [
                            AnalysisRunStatus.CREATED.value,
                            AnalysisRunStatus.ACTIVE.value,
                            AnalysisRunStatus.WAITING.value,
                        ]
                    },
                    "expires_at": {"$ne": None, "$lte": now},
                    "$or": [
                        {"worker_id": None},
                        {"lease_expires_at": None},
                        {"lease_expires_at": {"$lte": now}},
                    ],
                },
            )
        except AnalysisRunConflictError:
            duplicate = await self._idempotent_event_result(
                user_id=user_id,
                run_id=run_id,
                deduplication_key=deduplication_key,
                event_type=AnalysisEventType.RUN_EXPIRED,
                payload=payload,
                expected_status=AnalysisRunStatus.EXPIRED,
                expected_phase=AnalysisRunPhase.COMPLETED,
                expected_outcome=AnalysisRunOutcome.EXPIRED,
            )
            if duplicate is not None:
                return duplicate
            latest = await self.require_run(user_id=user_id, run_id=run_id)
            if latest.status in TERMINAL_RUN_STATUSES:
                return RunMutationResult(
                    run=latest,
                    event=None,
                    changed=False,
                )
            raise

    async def expire_due_runs(
        self,
        *,
        limit: int = 100,
    ) -> tuple[RunMutationResult, ...]:
        """Sweep one bounded batch; each run/event pair commits atomically."""

        candidates = await self._store.list_expirable_runs(
            current_time=self._now(),
            limit=limit,
        )
        results: list[RunMutationResult] = []
        for run in candidates:
            try:
                result = await self.expire_run(
                    user_id=run.user_id,
                    run_id=run.run_id,
                )
            except (
                AnalysisRunConflictError,
                AnalysisRunLeaseConflictError,
                AnalysisRunNotFoundError,
                InvalidAnalysisRunTransition,
            ):
                # Completion, renewal, cancellation, and another sweeper can
                # all legitimately win after the bounded candidate read.
                continue
            results.append(result)
        return tuple(results)

    async def finalize_requested_cancellation(
        self,
        *,
        user_id: str,
        run_id: str,
        trace_id: str | None = None,
    ) -> RunMutationResult:
        """Terminalize a requested cancellation after its worker is gone.

        This path intentionally ignores an expired lease. CAS still prevents
        it from racing a worker lifecycle commit, and cancellation_requested
        prevents that worker from emitting further progress.
        """

        current = await self.require_run(user_id=user_id, run_id=run_id)
        if current.status in TERMINAL_RUN_STATUSES:
            return RunMutationResult(run=current, event=None, changed=False)
        if not current.cancellation_requested:
            raise InvalidAnalysisRunTransition(
                "run does not have a pending cancellation request"
            )
        now = self._operation_time(current)
        if (
            current.worker_id is not None
            and current.lease_expires_at is not None
            and current.lease_expires_at > now
        ):
            raise AnalysisRunLeaseConflictError(
                "the active worker still owns the cancellation handoff"
            )
        return await self._store.mutate_with_event(
            user_id=user_id,
            run_id=run_id,
            expected_version=current.version,
            updates={
                "status": AnalysisRunStatus.CANCELLED,
                "phase": AnalysisRunPhase.COMPLETED,
                "outcome": AnalysisRunOutcome.CANCELLED,
                "updated_at": now,
                "completed_at": now,
                "worker_id": None,
                "lease_expires_at": None,
                "pause_requested": False,
                "pause_requested_at": None,
                "paused_at": None,
                "paused_from_status": None,
                "paused_from_phase": None,
                "paused_from_outcome": None,
            },
            event_type=AnalysisEventType.RUN_CANCELLED,
            payload={"reason": "user_requested"},
            deduplication_key=f"cancelled:{run_id}",
            trace_id=trace_id,
            additional_filter={
                "cancellation_requested": True,
                "status": {
                    "$nin": [item.value for item in TERMINAL_RUN_STATUSES]
                },
                "$or": [
                    {"worker_id": None},
                    {"lease_expires_at": None},
                    {"lease_expires_at": {"$lte": now}},
                ],
            },
        )

    async def list_abandoned_cancellations(
        self,
        *,
        limit: int = 100,
    ) -> tuple[AnalysisRun, ...]:
        return await self._store.list_abandoned_cancellations(
            current_time=self._now(),
            limit=limit,
        )

    async def list_abandoned_pauses(
        self,
        *,
        limit: int = 100,
    ) -> tuple[AnalysisRun, ...]:
        return await self._store.list_abandoned_pauses(
            current_time=self._now(),
            limit=limit,
        )


__all__ = [
    "AnalysisRunStateMachine",
    "InvalidAnalysisRunTransition",
]
