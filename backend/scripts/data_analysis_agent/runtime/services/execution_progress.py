"""Turning execution milestones into durable run events (Phase 9.14.2).

This is the only module that speaks both vocabularies: the engine's — datasets,
steps, results — and the run lifecycle's — event types, phases, deduplication
keys, leases. Keeping the translation here is what lets
`runtime/execution/` stay free of lifecycle concepts.

Three rules govern what gets written:

*Identifiers and counts only.* 9.14.2 is explicit that events carry IDs, counts,
status, safe warnings and durations — never rows, values or formulas. The
milestone types make that structural: there is no field on any of them in which
a cell value could travel.

*Progress must never fail an execution.* A run that computed a correct result
and then could not append a progress event has still computed a correct result.
Every append is best-effort and logged; the failure modes are all expected ones
— the run was paused or cancelled mid-flight, or another worker took the lease,
and in each case the event is genuinely no longer wanted.

*Deduplication keys are stable across replay.* A recovered attempt re-emits the
same milestones, so each key is scoped by lease attempt and by the milestone's
own identity. Replaying an attempt therefore updates nothing rather than
appending a second copy.
"""

from __future__ import annotations

import logging
from typing import Mapping

from pydantic import JsonValue

from ..execution.progress import (
    ExecutionMilestone,
    InputsResolved,
    ResultMaterialized,
    ResultValidated,
    ResultValidationStarted,
    StepCompleted,
)
from ..models.events import AnalysisEventType
from ..models.runs import AnalysisRun, AnalysisRunPhase
from ..observability.logging import log_analysis_event
from .state_machine import AnalysisRunStateMachine


logger = logging.getLogger(__name__)


def _describe(
    milestone: ExecutionMilestone,
) -> tuple[AnalysisEventType, AnalysisRunPhase, str, dict[str, JsonValue]]:
    """Map one milestone to its event type, phase, dedup suffix and payload."""

    if isinstance(milestone, InputsResolved):
        return (
            AnalysisEventType.EXECUTION_INPUTS_RESOLVED,
            AnalysisRunPhase.EXECUTION,
            "execution-inputs-resolved",
            {
                "dataset_count": milestone.dataset_count,
                "total_rows": milestone.total_rows,
            },
        )
    if isinstance(milestone, StepCompleted):
        return (
            AnalysisEventType.EXECUTION_STEP_COMPLETED,
            AnalysisRunPhase.EXECUTION,
            f"execution-step-{milestone.index}",
            {
                "step_id": milestone.step_id,
                "kind": milestone.kind,
                "index": milestone.index,
                "total": milestone.total,
                "input_rows": milestone.input_rows,
                "output_rows": milestone.output_rows,
                "output_columns": milestone.output_columns,
                "removed_rows": milestone.removed_rows,
            },
        )
    if isinstance(milestone, ResultValidationStarted):
        return (
            AnalysisEventType.RESULT_VALIDATION_STARTED,
            AnalysisRunPhase.RESULT_VALIDATION,
            "result-validation-started",
            {
                "row_count": milestone.row_count,
                "column_count": milestone.column_count,
            },
        )
    if isinstance(milestone, ResultValidated):
        return (
            AnalysisEventType.RESULT_VALIDATION_COMPLETED,
            AnalysisRunPhase.RESULT_VALIDATION,
            "result-validation-completed",
            {
                "row_count": milestone.row_count,
                "column_count": milestone.column_count,
            },
        )
    if isinstance(milestone, ResultMaterialized):
        return (
            AnalysisEventType.RESULT_MATERIALIZED,
            AnalysisRunPhase.RESULT_VALIDATION,
            "result-materialized",
            {
                "row_count": milestone.row_count,
                "column_count": milestone.column_count,
                "byte_count": milestone.byte_count,
                "content_hash": milestone.content_hash,
            },
        )
    raise TypeError(f"unknown execution milestone: {type(milestone).__name__}")


class DurableExecutionProgressReporter:
    """Appends execution milestones to the run's durable event stream."""

    def __init__(
        self,
        *,
        state_machine: AnalysisRunStateMachine,
        run: AnalysisRun,
        worker_id: str,
        lease_attempt: int,
    ) -> None:
        self._state_machine = state_machine
        self._run = run
        self._worker_id = worker_id
        self._lease_attempt = lease_attempt

    async def emit(self, milestone: ExecutionMilestone) -> None:
        event_type, phase, suffix, payload = _describe(milestone)
        try:
            await self._state_machine.record_event(
                user_id=self._run.user_id,
                run_id=self._run.run_id,
                event_type=event_type,
                phase=phase,
                payload=payload,
                deduplication_key=f"attempt-{self._lease_attempt}:{suffix}",
                worker_id=self._worker_id,
                lease_attempt=self._lease_attempt,
            )
        except Exception as error:  # noqa: BLE001 - progress is best-effort
            # A paused, cancelled or re-leased run legitimately refuses new
            # progress events. Losing one must never cost the result.
            self._log_dropped(event_type, error)

    def _log_dropped(
        self,
        event_type: AnalysisEventType,
        error: Exception,
    ) -> None:
        log_analysis_event(
            logger,
            "execution_progress_dropped",
            run_id=self._run.run_id,
            workspace_id=self._run.workspace_id,
            phase=AnalysisRunPhase.EXECUTION.value,
            operation=event_type.value,
            error_code=type(error).__name__,
        )


def milestone_payload(milestone: ExecutionMilestone) -> Mapping[str, JsonValue]:
    """The payload one milestone would append. Exposed for tests and taps."""

    return _describe(milestone)[3]


__all__ = [
    "DurableExecutionProgressReporter",
    "milestone_payload",
]
