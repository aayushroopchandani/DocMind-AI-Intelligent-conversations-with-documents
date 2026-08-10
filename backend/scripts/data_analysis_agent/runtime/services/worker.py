from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4

from ..integration import (
    Phase7AnalysisAdapter,
    Phase7ExecutionCancelled,
    Phase7ExecutionResult,
    Phase7Progress,
    Phase7ProgressReporter,
)
from ..models import (
    AnalysisEventType,
    AnalysisRun,
    AnalysisRunOutcome,
    AnalysisRunPhase,
    AnalysisRunStatus,
    DatasetCatalogEntry,
    DatasetHandle,
    RunIssueSummary,
    RunApprovalStatus,
    TokenUsage,
    TERMINAL_RUN_STATUSES,
)
from ..planning.contracts import (
    PlanningExecutionResult,
    PlanningOutcome,
    PlanningProgress,
    PlanningProgressReporter,
)
from ..planning.service import AnalysisPlanningService
from ..observability.tokens import merge_stage_maps, total_token_usage
from ..observability.logging import get_analysis_logger, log_analysis_event
from ..observability.metrics import analysis_metrics
from ..repositories.datasets import DatasetCatalogRepository
from ..repositories.runs import (
    AnalysisRunConflictError,
    AnalysisRunLeaseConflictError,
    AnalysisRunNotFoundError,
    AnalysisRunStoreError,
)
from .state_machine import (
    AnalysisRunStateMachine,
    InvalidAnalysisRunTransition,
)


logger = get_analysis_logger(__name__)

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


def _furthest_phase(
    current: AnalysisRunPhase,
    requested: AnalysisRunPhase,
) -> AnalysisRunPhase:
    if _PHASE_RANK[current] >= _PHASE_RANK[requested]:
        return current
    return requested


@dataclass(frozen=True, slots=True)
class AnalysisWorkerConfig:
    concurrency: int = 2
    poll_seconds: float = 0.75
    lease_seconds: int = 60
    renew_seconds: int = 20
    recovery_batch_size: int = 50

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ValueError("worker concurrency must be positive")
        if self.poll_seconds <= 0:
            raise ValueError("worker poll_seconds must be positive")
        if self.lease_seconds < 3:
            raise ValueError("worker lease_seconds must be at least three")
        if not 1 <= self.renew_seconds < self.lease_seconds:
            raise ValueError("lease renewal must occur before lease expiry")
        if not 1 <= self.recovery_batch_size <= 500:
            raise ValueError("recovery_batch_size must be between 1 and 500")


class _DurableProgressReporter(Phase7ProgressReporter):
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

    async def emit(self, progress: Phase7Progress) -> None:
        current = await self._state_machine.require_run(
            user_id=self._run.user_id,
            run_id=self._run.run_id,
        )
        await self._state_machine.record_event(
            user_id=self._run.user_id,
            run_id=self._run.run_id,
            event_type=progress.event_type,
            # A recovered attempt replays the graph from an immutable input
            # boundary. Its early milestones must not move the durable phase
            # backward from a later phase committed by the previous worker.
            phase=_furthest_phase(current.phase, progress.phase),
            payload=progress.payload,
            deduplication_key=(
                f"attempt-{self._lease_attempt}:{progress.deduplication_key}"
            ),
            worker_id=self._worker_id,
            lease_attempt=self._lease_attempt,
        )


class _DurablePlanningReporter(PlanningProgressReporter):
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

    async def emit(self, progress: PlanningProgress) -> None:
        current = await self._state_machine.require_run(
            user_id=self._run.user_id,
            run_id=self._run.run_id,
        )
        await self._state_machine.record_event(
            user_id=self._run.user_id,
            run_id=self._run.run_id,
            event_type=progress.event_type,
            phase=_furthest_phase(current.phase, progress.phase),
            payload=progress.payload,
            deduplication_key=(
                f"attempt-{self._lease_attempt}:{progress.deduplication_key}"
            ),
            worker_id=self._worker_id,
            lease_attempt=self._lease_attempt,
        )


class DurableAnalysisWorker:
    """Mongo-leased background executor for the implemented Phase 1–7 graph.

    SSE clients only observe this worker. Disconnecting a client cannot cancel
    execution, and another backend process can recover a run after lease expiry.
    """

    def __init__(
        self,
        *,
        state_machine: AnalysisRunStateMachine,
        dataset_catalog: DatasetCatalogRepository,
        adapter: Phase7AnalysisAdapter | None = None,
        planning_service: AnalysisPlanningService | None = None,
        config: AnalysisWorkerConfig | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._state_machine = state_machine
        self._dataset_catalog = dataset_catalog
        self._adapter = adapter or Phase7AnalysisAdapter()
        self._planning_service = planning_service
        self._config = config or AnalysisWorkerConfig()
        self._worker_id = worker_id or f"analysis-worker-{uuid4()}"
        self._stop = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._active: dict[str, asyncio.Task[None]] = {}

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def running(self) -> bool:
        return self._poll_task is not None and not self._poll_task.done()

    @property
    def active_run_count(self) -> int:
        return len(self._active)

    @property
    def concurrency(self) -> int:
        return self._config.concurrency

    async def start(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            return
        self._stop.clear()
        self._poll_task = asyncio.create_task(
            self._poll(),
            name=f"{self._worker_id}:poll",
        )

    async def stop(self) -> None:
        self._stop.set()
        poll_task = self._poll_task
        self._poll_task = None
        if poll_task is not None:
            poll_task.cancel()
        tasks = tuple(self._active.values())
        for task in tasks:
            task.cancel()
        if poll_task is not None or tasks:
            await asyncio.gather(
                *((poll_task,) if poll_task is not None else ()),
                *tasks,
                return_exceptions=True,
            )
        self._active.clear()

    async def _poll(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await self._reconcile_abandoned_pauses()
                    await self._reconcile_abandoned_cancellations()
                    await self._reconcile_expired_runs()
                    self._remove_finished_tasks()
                    capacity = self._config.concurrency - len(self._active)
                    if capacity > 0:
                        candidates = (
                            await self._state_machine.list_recoverable_runs(
                                limit=min(
                                    self._config.recovery_batch_size,
                                    max(capacity * 4, capacity),
                                )
                            )
                        )
                        for run in candidates:
                            if capacity <= 0:
                                break
                            if run.run_id in self._active:
                                continue
                            task = asyncio.create_task(
                                self._process_candidate(run),
                                name=f"{self._worker_id}:{run.run_id}",
                            )
                            self._active[run.run_id] = task
                            capacity -= 1
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Mongo/provider outages must not silently kill the
                    # embedded worker forever. Durable leases make retrying
                    # this polling iteration safe.
                    logger.exception(
                        "durable analysis worker polling iteration failed"
                    )
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self._config.poll_seconds,
                    )
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise

    def _remove_finished_tasks(self) -> None:
        for run_id, task in tuple(self._active.items()):
            if not task.done():
                continue
            self._active.pop(run_id, None)
            try:
                task.result()
            except asyncio.CancelledError:
                continue
            except Exception:
                logger.exception(
                    "analysis run task escaped its execution boundary",
                    extra={"run_id": run_id},
                )

    async def _reconcile_abandoned_cancellations(self) -> None:
        runs = await self._state_machine.list_abandoned_cancellations(
            limit=self._config.recovery_batch_size,
        )
        for run in runs:
            try:
                await self._state_machine.finalize_requested_cancellation(
                    user_id=run.user_id,
                    run_id=run.run_id,
                )
            except (
                AnalysisRunConflictError,
                AnalysisRunLeaseConflictError,
                AnalysisRunNotFoundError,
            ):
                continue

    async def _reconcile_abandoned_pauses(self) -> None:
        runs = await self._state_machine.list_abandoned_pauses(
            limit=self._config.recovery_batch_size,
        )
        for run in runs:
            try:
                await self._state_machine.finalize_requested_pause(
                    user_id=run.user_id,
                    run_id=run.run_id,
                )
            except (
                AnalysisRunConflictError,
                AnalysisRunLeaseConflictError,
                AnalysisRunNotFoundError,
                InvalidAnalysisRunTransition,
            ):
                continue

    async def _reconcile_expired_runs(self) -> None:
        await self._state_machine.expire_due_runs(
            limit=self._config.recovery_batch_size,
        )

    async def _process_candidate(self, candidate: AnalysisRun) -> None:
        # Repository filtering is the primary queue gate. Keep this defensive
        # check because tests, maintenance tools, and future queue adapters may
        # invoke candidate processing directly.
        if not candidate.inputs_ready:
            return
        claim_started = monotonic()
        try:
            claimed = await self._state_machine.claim_execution(
                user_id=candidate.user_id,
                run_id=candidate.run_id,
                worker_id=self._worker_id,
                lease_seconds=self._config.lease_seconds,
                expected_version=candidate.version,
            )
        except (
            AnalysisRunConflictError,
            AnalysisRunLeaseConflictError,
            AnalysisRunNotFoundError,
        ):
            return

        run = claimed.run
        lease_attempt = run.lease_attempt
        log_analysis_event(
            logger,
            "run_claimed",
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            phase=run.phase.value,
            operation="execute",
            lease_attempt=lease_attempt,
            status=run.status.value,
        )
        lease_lost = asyncio.Event()
        assert run.lease_expires_at is not None
        committed_lease_seconds = max(
            0.0,
            (run.lease_expires_at - run.updated_at).total_seconds(),
        )
        run_deadline = (
            claim_started
            + max(
                0.0,
                (run.expires_at - run.updated_at).total_seconds(),
            )
            if run.expires_at is not None
            else None
        )
        renewer = asyncio.create_task(
            self._renew_lease(
                run=run,
                lease_attempt=lease_attempt,
                lease_lost=lease_lost,
                lease_deadline=(
                    claim_started + committed_lease_seconds
                ),
                run_deadline=run_deadline,
            ),
            name=f"{self._worker_id}:{run.run_id}:lease",
        )
        execution = asyncio.create_task(
            self._execute_claimed(
                run=run,
                lease_attempt=lease_attempt,
                lease_lost=lease_lost,
            ),
            name=f"{self._worker_id}:{run.run_id}:execution",
        )
        try:
            done, _ = await asyncio.wait(
                (execution, renewer),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if renewer in done and not execution.done():
                # Fencing is useful only if the old attempt stops immediately.
                # Cancelling `anext(graph_stream)` propagates into LangGraph
                # instead of allowing a lost worker to keep issuing LLM calls
                # or durable writes until the next graph milestone.
                lease_lost.set()
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                await self._finish_interrupted(run, lease_attempt)
                return
            await execution
        except asyncio.CancelledError:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            await self._best_effort_release(run, lease_attempt)
            raise
        finally:
            renewer.cancel()
            execution.cancel()
            await asyncio.gather(
                renewer,
                execution,
                return_exceptions=True,
            )

    async def _renew_lease(
        self,
        *,
        run: AnalysisRun,
        lease_attempt: int,
        lease_lost: asyncio.Event,
        lease_deadline: float | None = None,
        run_deadline: float | None = None,
    ) -> None:
        known_deadline = (
            lease_deadline
            if lease_deadline is not None
            else monotonic() + self._config.lease_seconds
        )
        delay = float(self._config.renew_seconds)
        try:
            while True:
                remaining = known_deadline - monotonic()
                if remaining <= 0:
                    lease_lost.set()
                    return
                await asyncio.sleep(min(delay, remaining))
                if monotonic() >= known_deadline:
                    lease_lost.set()
                    return

                renewal_started = monotonic()
                try:
                    await asyncio.wait_for(
                        self._state_machine.renew_execution_lease(
                            user_id=run.user_id,
                            run_id=run.run_id,
                            worker_id=self._worker_id,
                            lease_attempt=lease_attempt,
                            lease_seconds=self._config.lease_seconds,
                        ),
                        timeout=max(
                            known_deadline - renewal_started,
                            0.001,
                        ),
                    )
                except TimeoutError:
                    # The last confirmed lease is now at (or extremely near)
                    # its deadline. An unknown provider outcome cannot justify
                    # continuing execution.
                    lease_lost.set()
                    return
                except (
                    AnalysisRunConflictError,
                    AnalysisRunNotFoundError,
                ):
                    # A CAS/ownership miss is authoritative: this execution
                    # attempt must stop producing side effects immediately.
                    lease_lost.set()
                    return
                except AnalysisRunStoreError:
                    # A provider outage says nothing about ownership. Keep the
                    # attempt alive and retry frequently, but never beyond the
                    # last lease deadline confirmed by Mongo.
                    remaining = known_deadline - monotonic()
                    if remaining <= 0:
                        lease_lost.set()
                        return
                    logger.warning(
                        "analysis lease renewal temporarily unavailable; retrying",
                        extra={
                            "run_id": run.run_id,
                            "lease_attempt": lease_attempt,
                        },
                    )
                    delay = min(self._config.poll_seconds, remaining)
                    continue
                except Exception:
                    logger.exception(
                        "analysis lease renewal failed unexpectedly",
                        extra={
                            "run_id": run.run_id,
                            "lease_attempt": lease_attempt,
                        },
                    )
                    lease_lost.set()
                    return

                # The state machine requests a full lease interval. Anchor the
                # local monotonic deadline at request start so response latency
                # cannot make this worker overestimate its durable ownership.
                known_deadline = (
                    renewal_started + self._config.lease_seconds
                )
                if run_deadline is not None:
                    known_deadline = min(known_deadline, run_deadline)
                delay = float(self._config.renew_seconds)
        except asyncio.CancelledError:
            raise

    async def _execute_claimed(
        self,
        *,
        run: AnalysisRun,
        lease_attempt: int,
        lease_lost: asyncio.Event,
    ) -> None:
        started = monotonic()
        try:
            await self._record_context(
                run=run,
                lease_attempt=lease_attempt,
            )
            dataset_handles = await self._dataset_catalog.load_handles(
                user_id=run.user_id,
                workspace_id=run.workspace_id,
                versions=tuple(
                    (reference.dataset_id, reference.source_version)
                    for reference in run.input_dataset_versions
                ),
            )
            if len(dataset_handles) != len(run.input_dataset_versions):
                raise RuntimeError(
                    "one or more immutable input dataset versions are unavailable"
                )
            await self._record_resolved_sources(
                run=run,
                lease_attempt=lease_attempt,
                dataset_ids=tuple(
                    handle.dataset_id for handle in dataset_handles
                ),
            )

            async def is_cancelled() -> bool:
                if lease_lost.is_set() or self._stop.is_set():
                    return True
                latest = await self._state_machine.require_run(
                    user_id=run.user_id,
                    run_id=run.run_id,
                )
                return (
                    latest.cancellation_requested
                    or latest.pause_requested
                    or latest.status != AnalysisRunStatus.ACTIVE
                    or latest.worker_id != self._worker_id
                    or latest.lease_attempt != lease_attempt
                )

            result = await self._adapter.execute(
                run,
                dataset_handles=dataset_handles,
                reporter=_DurableProgressReporter(
                    state_machine=self._state_machine,
                    run=run,
                    worker_id=self._worker_id,
                    lease_attempt=lease_attempt,
                ),
                is_cancelled=is_cancelled,
            )
            await self._finish_result(
                run=run,
                lease_attempt=lease_attempt,
                result=result,
                dataset_handles=tuple(dataset_handles),
                elapsed_ms=(monotonic() - started) * 1000,
            )
        except Phase7ExecutionCancelled:
            await self._finish_interrupted(run, lease_attempt)
        except (
            AnalysisRunConflictError,
            AnalysisRunLeaseConflictError,
            InvalidAnalysisRunTransition,
        ):
            await self._finish_interrupted(run, lease_attempt)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "durable analysis execution failed",
                extra={"run_id": run.run_id},
            )
            await self._finish_failed(
                run=run,
                lease_attempt=lease_attempt,
                error=RunIssueSummary(
                    code="analysis_runtime_failed",
                    message="The data-analysis pipeline could not complete.",
                    retryable=True,
                ),
                elapsed_ms=(monotonic() - started) * 1000,
            )

    async def _record_context(
        self,
        *,
        run: AnalysisRun,
        lease_attempt: int,
    ) -> None:
        context_phase = await self._current_phase_at_least(
            run=run,
            requested=AnalysisRunPhase.CONTEXT_RESOLUTION,
        )
        await self._state_machine.record_event(
            user_id=run.user_id,
            run_id=run.run_id,
            event_type=AnalysisEventType.CONTEXT_RESOLUTION_STARTED,
            phase=context_phase,
            payload={
                "artifact_version_count": len(
                    run.input_artifact_version_ids
                ),
                "dataset_version_count": len(run.input_dataset_versions),
                "document_count": len(run.selected_document_ids),
            },
            deduplication_key=f"attempt-{lease_attempt}:context-started",
            worker_id=self._worker_id,
            lease_attempt=lease_attempt,
        )
        for index, version_id in enumerate(run.input_artifact_version_ids):
            artifact_phase = await self._current_phase_at_least(
                run=run,
                requested=AnalysisRunPhase.CONTEXT_RESOLUTION,
            )
            await self._state_machine.record_event(
                user_id=run.user_id,
                run_id=run.run_id,
                event_type=AnalysisEventType.ARTIFACT_REGISTERED,
                phase=artifact_phase,
                payload={"artifact_version_id": version_id},
                deduplication_key=(
                    f"attempt-{lease_attempt}:artifact-{index}"
                ),
                worker_id=self._worker_id,
                lease_attempt=lease_attempt,
            )

    async def _record_resolved_sources(
        self,
        *,
        run: AnalysisRun,
        lease_attempt: int,
        dataset_ids: tuple[str, ...],
    ) -> None:
        for index, dataset_id in enumerate(dataset_ids):
            dataset_phase = await self._current_phase_at_least(
                run=run,
                requested=AnalysisRunPhase.CONTEXT_RESOLUTION,
            )
            await self._state_machine.record_event(
                user_id=run.user_id,
                run_id=run.run_id,
                event_type=AnalysisEventType.DATASET_REGISTERED,
                phase=dataset_phase,
                payload={"dataset_id": dataset_id},
                deduplication_key=f"attempt-{lease_attempt}:dataset-{index}",
                worker_id=self._worker_id,
                lease_attempt=lease_attempt,
            )
        resolved_phase = await self._current_phase_at_least(
            run=run,
            requested=AnalysisRunPhase.EVIDENCE_PREPARATION,
        )
        await self._state_machine.record_event(
            user_id=run.user_id,
            run_id=run.run_id,
            event_type=AnalysisEventType.CONTEXT_RESOLVED,
            phase=resolved_phase,
            payload={
                "dataset_count": len(dataset_ids),
                "document_count": len(run.selected_document_ids),
            },
            deduplication_key=f"attempt-{lease_attempt}:context-resolved",
            worker_id=self._worker_id,
            lease_attempt=lease_attempt,
        )

    async def _current_phase_at_least(
        self,
        *,
        run: AnalysisRun,
        requested: AnalysisRunPhase,
    ) -> AnalysisRunPhase:
        current = await self._state_machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        return _furthest_phase(current.phase, requested)

    async def _finish_result(
        self,
        *,
        run: AnalysisRun,
        lease_attempt: int,
        result: Phase7ExecutionResult,
        dataset_handles: tuple[DatasetHandle, ...],
        elapsed_ms: float,
    ) -> None:
        analysis_metrics.observe_phase("phase_1_to_7", elapsed_ms)
        summary = {
            "warnings_summary": result.warnings,
            "errors_summary": result.errors,
            "final_dataset_ids": result.final_dataset_ids,
            "model_versions": result.model_versions,
            "prompt_versions": result.prompt_versions,
            "token_usage": result.token_usage,
            "token_usage_by_stage": result.token_usage_by_stage,
            "timings_ms": {"phase_1_to_7": elapsed_ms},
        }
        payload = {
            "outcome": result.outcome.value,
            "prepared_dataset_count": result.prepared_dataset_count,
            "total_input_rows": result.total_input_rows,
            "total_output_rows": result.total_output_rows,
        }
        if result.outcome == AnalysisRunOutcome.DATASETS_PREPARED:
            if self._planning_service is not None:
                await self._finish_planning(
                    run=run,
                    lease_attempt=lease_attempt,
                    result=result,
                    dataset_handles=dataset_handles,
                    phase7_elapsed_ms=elapsed_ms,
                )
                return
            await self._state_machine.transition(
                user_id=run.user_id,
                run_id=run.run_id,
                target_status=AnalysisRunStatus.SUCCEEDED,
                target_phase=AnalysisRunPhase.COMPLETED,
                outcome=result.outcome,
                event_type=AnalysisEventType.RUN_COMPLETED,
                payload=payload,
                deduplication_key=f"attempt-{lease_attempt}:completed",
                worker_id=self._worker_id,
                lease_attempt=lease_attempt,
                summary_updates=summary,
            )
            return
        if result.outcome == AnalysisRunOutcome.UNANSWERABLE:
            await self._state_machine.transition(
                user_id=run.user_id,
                run_id=run.run_id,
                target_status=AnalysisRunStatus.SUCCEEDED,
                target_phase=AnalysisRunPhase.COMPLETED,
                outcome=result.outcome,
                event_type=AnalysisEventType.RUN_COMPLETED,
                payload=payload,
                deduplication_key=f"attempt-{lease_attempt}:unanswerable",
                worker_id=self._worker_id,
                lease_attempt=lease_attempt,
                summary_updates=summary,
            )
            return
        if result.outcome == AnalysisRunOutcome.CLARIFICATION_REQUIRED:
            current = await self._state_machine.require_run(
                user_id=run.user_id,
                run_id=run.run_id,
            )
            await self._state_machine.transition(
                user_id=run.user_id,
                run_id=run.run_id,
                target_status=AnalysisRunStatus.WAITING,
                target_phase=current.phase,
                outcome=result.outcome,
                event_type=AnalysisEventType.CLARIFICATION_REQUIRED,
                payload=payload,
                deduplication_key=f"attempt-{lease_attempt}:clarification",
                worker_id=self._worker_id,
                lease_attempt=lease_attempt,
                summary_updates=summary,
            )
            return
        error = (
            result.errors[0]
            if result.errors
            else RunIssueSummary(
                code="phase7_pipeline_failed",
                message="The data-analysis pipeline could not complete.",
                retryable=False,
            )
        )
        await self._finish_failed(
            run=run,
            lease_attempt=lease_attempt,
            error=error,
            warnings=result.warnings,
            errors=result.errors,
            elapsed_ms=elapsed_ms,
        )

    async def _register_planning_sources(
        self,
        *,
        initial: tuple[DatasetHandle, ...],
        discovered: tuple[DatasetHandle, ...],
    ) -> tuple[DatasetHandle, ...]:
        """Durably register Phase-7 PDF sources before plan construction."""

        by_id = {item.dataset_id: item for item in initial}
        pending: list[DatasetHandle] = []
        for handle in discovered:
            existing = by_id.get(handle.dataset_id)
            if existing is not None:
                if existing.source_version != handle.source_version:
                    raise RuntimeError(
                        "one dataset resolved to conflicting immutable versions"
                    )
                continue
            by_id[handle.dataset_id] = handle
            pending.append(handle)
        if pending:
            registered = await asyncio.gather(
                *(
                    self._dataset_catalog.register(
                        DatasetCatalogEntry(
                            handle=handle,
                            discovery_summary=(
                                "PDF table selected by the evidence-preparation "
                                f"pipeline: {handle.title}"
                            ),
                        )
                    )
                    for handle in pending
                )
            )
            for entry in registered:
                by_id[entry.handle.dataset_id] = entry.handle
        return tuple(by_id.values())

    async def _finish_planning(
        self,
        *,
        run: AnalysisRun,
        lease_attempt: int,
        result: Phase7ExecutionResult,
        dataset_handles: tuple[DatasetHandle, ...],
        phase7_elapsed_ms: float,
    ) -> None:
        artifacts = result.planning_artifacts
        if artifacts is None:
            await self._finish_failed(
                run=run,
                lease_attempt=lease_attempt,
                error=RunIssueSummary(
                    code="planning_artifacts_missing",
                    message="Prepared evidence did not include planning artifacts.",
                    retryable=False,
                ),
                warnings=result.warnings,
                errors=result.errors,
                elapsed_ms=phase7_elapsed_ms,
            )
            return
        planning_handles = await self._register_planning_sources(
            initial=dataset_handles,
            discovered=artifacts.source_dataset_handles,
        )
        planning_started = monotonic()
        assert self._planning_service is not None
        planning = await self._planning_service.create_plan(
            run=run,
            dataset_handles=planning_handles,
            requirements=artifacts.requirements,
            profiles=artifacts.dataset_profiles,
            normalization=artifacts.normalization,
            reporter=_DurablePlanningReporter(
                state_machine=self._state_machine,
                run=run,
                worker_id=self._worker_id,
                lease_attempt=lease_attempt,
            ),
        )
        planning_elapsed_ms = (monotonic() - planning_started) * 1000
        analysis_metrics.observe_phase("planning", planning_elapsed_ms)
        token_usage_by_stage = merge_stage_maps(
            result.token_usage_by_stage,
            planning.token_usage_by_stage,
        )
        token_usage = (
            total_token_usage(token_usage_by_stage)
            if token_usage_by_stage
            else _merge_usage(result.token_usage, planning.token_usage)
        )
        model_versions = {
            **result.model_versions,
            **(
                {"planner": planning.plan.model}
                if planning.plan is not None
                else {}
            ),
        }
        prompt_versions = {
            **result.prompt_versions,
            **(
                {"planner": planning.plan.prompt_version}
                if planning.plan is not None
                else {}
            ),
        }
        base_summary = {
            "warnings_summary": (*result.warnings, *planning.warnings),
            "errors_summary": (*result.errors, *planning.errors),
            "final_dataset_ids": result.final_dataset_ids,
            "model_versions": model_versions,
            "prompt_versions": prompt_versions,
            "token_usage": token_usage,
            "token_usage_by_stage": token_usage_by_stage,
            "privacy_summary": (
                planning.plan.privacy
                if planning.plan is not None
                else run.privacy_summary
            ),
            "timings_ms": {
                "phase_1_to_7": phase7_elapsed_ms,
                "planning": planning_elapsed_ms,
            },
        }
        if planning.outcome == PlanningOutcome.FAILED:
            error = (
                planning.errors[0]
                if planning.errors
                else RunIssueSummary(
                    code="planning_failed",
                    message="A safe analysis plan could not be generated.",
                    retryable=True,
                )
            )
            await self._state_machine.transition(
                user_id=run.user_id,
                run_id=run.run_id,
                target_status=AnalysisRunStatus.FAILED,
                target_phase=AnalysisRunPhase.COMPLETED,
                outcome=AnalysisRunOutcome.FAILED,
                event_type=AnalysisEventType.RUN_FAILED,
                payload={
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                },
                deduplication_key=f"attempt-{lease_attempt}:planning-failed",
                worker_id=self._worker_id,
                lease_attempt=lease_attempt,
                summary_updates=base_summary,
            )
            analysis_metrics.record_error(error.code)
            analysis_metrics.record_run_outcome("failed")
            log_analysis_event(
                logger,
                "run_failed",
                level=logging.ERROR,
                run_id=run.run_id,
                workspace_id=run.workspace_id,
                phase=AnalysisRunPhase.PLAN_VALIDATION.value,
                operation="planning",
                duration_ms=planning_elapsed_ms,
                error_code=error.code,
                outcome="failed",
            )
            return
        if planning.outcome == PlanningOutcome.CLARIFICATION_REQUIRED:
            await self._state_machine.transition(
                user_id=run.user_id,
                run_id=run.run_id,
                target_status=AnalysisRunStatus.WAITING,
                target_phase=AnalysisRunPhase.PLAN_VALIDATION,
                outcome=AnalysisRunOutcome.CLARIFICATION_REQUIRED,
                event_type=AnalysisEventType.CLARIFICATION_REQUIRED,
                payload={
                    "stage": "plan_validation",
                    "message": planning.clarification or "Clarification is required.",
                    "validation_attempts": len(planning.reports),
                },
                deduplication_key=(
                    f"attempt-{lease_attempt}:planning-clarification"
                ),
                worker_id=self._worker_id,
                lease_attempt=lease_attempt,
                summary_updates=base_summary,
            )
            return
        plan = planning.plan
        assert plan is not None
        approval_status = (
            RunApprovalStatus.PENDING
            if planning.outcome == PlanningOutcome.APPROVAL_REQUIRED
            else RunApprovalStatus.NOT_REQUIRED
        )
        plan_summary = {
            **base_summary,
            "current_plan_id": plan.plan_id,
            "current_plan_revision": plan.revision,
            "current_plan_hash": plan.plan_hash,
            "plan_approval_status": approval_status,
        }
        payload = {
            "plan_id": plan.plan_id,
            "revision": plan.revision,
            "plan_hash": plan.plan_hash,
            "step_count": len(plan.steps),
            "approval_required": plan.approval_policy.plan_approval_required,
            "approval_reasons": [
                reason.value
                for reason in plan.approval_policy.plan_approval_reasons
            ],
            "final_patch_approval_required": (
                plan.approval_policy.final_patch_approval_required
            ),
        }
        if planning.outcome == PlanningOutcome.APPROVAL_REQUIRED:
            await self._state_machine.transition(
                user_id=run.user_id,
                run_id=run.run_id,
                target_status=AnalysisRunStatus.WAITING,
                target_phase=AnalysisRunPhase.APPROVAL,
                outcome=AnalysisRunOutcome.PLAN_READY,
                event_type=AnalysisEventType.PLAN_APPROVAL_REQUIRED,
                payload=payload,
                deduplication_key=(
                    f"attempt-{lease_attempt}:plan-approval-required"
                ),
                worker_id=self._worker_id,
                lease_attempt=lease_attempt,
                summary_updates=plan_summary,
            )
            log_analysis_event(
                logger,
                "approval_required",
                run_id=run.run_id,
                workspace_id=run.workspace_id,
                phase=AnalysisRunPhase.APPROVAL.value,
                operation="planning",
                plan_step_count=len(plan.steps),
                duration_ms=planning_elapsed_ms,
                status=AnalysisRunStatus.WAITING.value,
            )
            return
        await self._state_machine.transition(
            user_id=run.user_id,
            run_id=run.run_id,
            target_status=AnalysisRunStatus.SUCCEEDED,
            target_phase=AnalysisRunPhase.COMPLETED,
            outcome=AnalysisRunOutcome.PLAN_READY,
            event_type=AnalysisEventType.PLAN_READY,
            payload=payload,
            deduplication_key=f"attempt-{lease_attempt}:plan-ready",
            worker_id=self._worker_id,
            lease_attempt=lease_attempt,
            summary_updates=plan_summary,
        )
        analysis_metrics.record_run_outcome("plan_ready")
        log_analysis_event(
            logger,
            "run_completed",
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            phase=AnalysisRunPhase.COMPLETED.value,
            operation="planning",
            plan_step_count=len(plan.steps),
            duration_ms=planning_elapsed_ms,
            outcome="plan_ready",
            status=AnalysisRunStatus.SUCCEEDED.value,
        )

    async def _finish_failed(
        self,
        *,
        run: AnalysisRun,
        lease_attempt: int,
        error: RunIssueSummary,
        warnings: tuple[RunIssueSummary, ...] = (),
        errors: tuple[RunIssueSummary, ...] = (),
        elapsed_ms: float,
    ) -> None:
        try:
            await self._state_machine.transition(
                user_id=run.user_id,
                run_id=run.run_id,
                target_status=AnalysisRunStatus.FAILED,
                target_phase=AnalysisRunPhase.COMPLETED,
                outcome=AnalysisRunOutcome.FAILED,
                event_type=AnalysisEventType.RUN_FAILED,
                payload={
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                },
                deduplication_key=f"attempt-{lease_attempt}:failed",
                worker_id=self._worker_id,
                lease_attempt=lease_attempt,
                summary_updates={
                    "warnings_summary": warnings,
                    "errors_summary": errors or (error,),
                    "timings_ms": {"phase_1_to_7": elapsed_ms},
                },
            )
            analysis_metrics.record_error(error.code)
            analysis_metrics.record_run_outcome("failed")
            log_analysis_event(
                logger,
                "run_failed",
                level=logging.ERROR,
                run_id=run.run_id,
                workspace_id=run.workspace_id,
                phase=AnalysisRunPhase.COMPLETED.value,
                operation="execute",
                duration_ms=elapsed_ms,
                error_code=error.code,
                outcome="failed",
            )
        except (
            AnalysisRunConflictError,
            AnalysisRunLeaseConflictError,
            InvalidAnalysisRunTransition,
        ):
            await self._finish_interrupted(run, lease_attempt)

    async def _finish_cancelled(
        self,
        run: AnalysisRun,
        lease_attempt: int,
    ) -> None:
        latest = await self._state_machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        if latest.status in TERMINAL_RUN_STATUSES:
            return
        if not latest.cancellation_requested:
            return
        try:
            await self._state_machine.transition(
                user_id=run.user_id,
                run_id=run.run_id,
                target_status=AnalysisRunStatus.CANCELLED,
                target_phase=AnalysisRunPhase.COMPLETED,
                outcome=AnalysisRunOutcome.CANCELLED,
                event_type=AnalysisEventType.RUN_CANCELLED,
                payload={"reason": "user_requested"},
                deduplication_key=f"attempt-{lease_attempt}:cancelled",
                worker_id=self._worker_id,
                lease_attempt=lease_attempt,
            )
            analysis_metrics.record_run_outcome("cancelled")
            log_analysis_event(
                logger,
                "run_cancelled",
                run_id=run.run_id,
                workspace_id=run.workspace_id,
                phase=AnalysisRunPhase.COMPLETED.value,
                operation="execute",
                outcome="cancelled",
                status=AnalysisRunStatus.CANCELLED.value,
            )
        except (AnalysisRunConflictError, AnalysisRunLeaseConflictError):
            return

    async def _finish_cancelled_if_requested(
        self,
        run: AnalysisRun,
        lease_attempt: int,
    ) -> None:
        try:
            await self._finish_cancelled(run, lease_attempt)
        except AnalysisRunNotFoundError:
            return

    async def _finish_interrupted(
        self,
        run: AnalysisRun,
        lease_attempt: int,
    ) -> None:
        """Honor cancel/pause in priority order at a worker-safe boundary."""

        try:
            latest = await self._state_machine.require_run(
                user_id=run.user_id,
                run_id=run.run_id,
            )
            if latest.status in TERMINAL_RUN_STATUSES:
                return
            if latest.cancellation_requested:
                await self._finish_cancelled(run, lease_attempt)
                return
            if latest.pause_requested:
                await self._state_machine.finalize_requested_pause(
                    user_id=run.user_id,
                    run_id=run.run_id,
                    worker_id=self._worker_id,
                    lease_attempt=lease_attempt,
                )
        except (
            AnalysisRunConflictError,
            AnalysisRunLeaseConflictError,
            AnalysisRunNotFoundError,
            InvalidAnalysisRunTransition,
        ):
            return

    async def _best_effort_release(
        self,
        run: AnalysisRun,
        lease_attempt: int,
    ) -> None:
        try:
            latest = await self._state_machine.require_run(
                user_id=run.user_id,
                run_id=run.run_id,
            )
            if (
                latest.status == AnalysisRunStatus.ACTIVE
                and latest.worker_id == self._worker_id
                and latest.lease_attempt == lease_attempt
            ):
                await self._state_machine.release_execution_lease(
                    user_id=run.user_id,
                    run_id=run.run_id,
                    worker_id=self._worker_id,
                    lease_attempt=lease_attempt,
                )
        except Exception:
            return


__all__ = [
    "AnalysisWorkerConfig",
    "DurableAnalysisWorker",
]


def _merge_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    input_tokens = left.input_tokens + right.input_tokens
    output_tokens = left.output_tokens + right.output_tokens
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimated_cost_usd=(
            left.estimated_cost_usd + right.estimated_cost_usd
        ),
    )
