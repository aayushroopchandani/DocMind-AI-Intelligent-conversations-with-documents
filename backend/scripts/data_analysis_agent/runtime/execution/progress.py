"""What an execution can say about itself while it runs (Phase 9.14.2).

The engine reports *milestones*, not run events. It knows about datasets, steps
and results; it does not know that a run has a phase, or that an event stream
exists. The translation into durable run events happens in
`runtime/services/execution_progress.py`, which is the only place that holds both
vocabularies.

That split is the point. It keeps lifecycle concepts out of the execution path,
lets the engine be driven by tests with a null reporter, and means a second
consumer — metrics, a log tap — can subscribe to the same milestones without
going through the run event stream.

**On liveness.** Milestones raised by the parent process are live: inputs are
resolved, then the milestone fires. Per-step milestones are not, and this module
does not pretend otherwise. The engine runs inside a bounded child process
(9.4.4) which reports its per-step counters only when it returns, so
:class:`StepCompleted` is replayed from those counters in execution order once
the child exits. Every field is still exactly what that step did; only the
arrival time is batched. Wiring a progress channel out of the child would change
where these are emitted, not what they say.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Union


@dataclass(frozen=True, slots=True)
class InputsResolved:
    """Every declared input was found and staged for the engine."""

    dataset_count: int
    total_rows: int


@dataclass(frozen=True, slots=True)
class StepCompleted:
    """One native step finished, with the shape it produced.

    `index` is one-based and `total` is the step count for the whole recipe, so
    a client can render progress without having read the plan.
    """

    step_id: str
    kind: str
    index: int
    total: int
    input_rows: int
    output_rows: int
    output_columns: int

    @property
    def removed_rows(self) -> int:
        return max(0, self.input_rows - self.output_rows)


@dataclass(frozen=True, slots=True)
class ResultValidationStarted:
    """The frame is complete; assertions and limits are about to be checked."""

    row_count: int
    column_count: int


@dataclass(frozen=True, slots=True)
class ResultValidated:
    """Every assertion and limit passed. Nothing is stored yet."""

    row_count: int
    column_count: int


@dataclass(frozen=True, slots=True)
class ResultMaterialized:
    """The bundle is durable in blob storage and content-addressed."""

    row_count: int
    column_count: int
    byte_count: int
    content_hash: str


ExecutionMilestone = Union[
    InputsResolved,
    StepCompleted,
    ResultValidationStarted,
    ResultValidated,
    ResultMaterialized,
]


class ExecutionProgressReporter(Protocol):
    async def emit(self, milestone: ExecutionMilestone) -> None: ...


class NullExecutionProgressReporter:
    """The default. Execution must not depend on anyone listening."""

    async def emit(self, milestone: ExecutionMilestone) -> None:
        del milestone


__all__ = [
    "ExecutionMilestone",
    "ExecutionProgressReporter",
    "InputsResolved",
    "NullExecutionProgressReporter",
    "ResultMaterialized",
    "ResultValidated",
    "ResultValidationStarted",
    "StepCompleted",
]
