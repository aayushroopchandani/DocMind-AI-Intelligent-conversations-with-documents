"""Driving one workbook edit from finished result to applied receipt.

This is the orchestrator for Phase 9.11 and 9.12. Everything it does is already
implemented somewhere else — placement decides where, the compiler builds what,
the repositories persist it, the state machine moves the run — and its whole job
is to run those in the one order that is safe:

    ask for context -> place -> reserve -> compile -> propose
                    -> approve -> preflight -> apply -> receipt

Two properties are worth stating because they are what the ordering buys:

*The rectangle is claimed before the patch is shown.* A user cannot approve a
target that another run took while they were reading, because the reservation
happens before the proposal is persisted.

*A run only completes on a verified receipt.* Not on approval, not on the client
saying it started — on a receipt whose hashes match the ones this server
computed. A partial application leaves the run waiting with its inverse intact
rather than reporting success.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from ..execution.results.reader import ExecutionResultReader
from ..models.events import AnalysisEventType
from ..models.patches import (
    PatchApprovalCommand,
    PatchDecision,
    PatchPlacementSummary,
    PatchProposal,
    utc_now,
)
from ..models.plans import (
    AnalysisPlan,
    PlanApprovalStatus,
    WorkbookPlacementPolicy,
    WorkbookWriteIntent,
)
from ..models.reservations import ReservationStatus
from ..models.runs import (
    AnalysisRun,
    AnalysisRunOutcome,
    AnalysisRunPhase,
    AnalysisRunStatus,
    RunApprovalStatus,
    TERMINAL_RUN_STATUSES,
)
from ..models.workbook import Rect
from ..patches.cells import CellState
from ..patches.compiler import PatchIdentity, compile_patch
from ..patches.conflicts import (
    ConflictAssessment,
    ConflictKind,
    ConflictResolution,
    assess_conflict,
    rebase_patch,
)
from ..patches.envelope import PatchStatus, WorkbookGuard
from ..patches.grid import ResultGrid
from ..patches.operations import ChunkedPayload, PayloadChunkReference
from ..patches.preview import build_patch_preview
from ..patches.receipt import PatchApplicationReceipt, ReceiptVerdict, verify_receipt
from ..patches.undo import build_undo_patch
from ..placement import (
    PlacementDecision,
    PlacementError,
    PlacementRequest,
    ReservationRequest,
    WorkbookPatchContext,
    WriteReservationService,
    select_placement,
)
from ..repositories.patches import PatchConflictError, PatchProposalRepository
from ..repositories.reservations import SpatialReservationConflictError
from .state_machine import AnalysisRunStateMachine


class PatchServiceError(RuntimeError):
    """The patch flow cannot continue for this run."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PatchNotReadyError(PatchServiceError):
    """The run is not at a point where this step is meaningful."""


@dataclass(frozen=True, slots=True)
class PatchServiceConfig:
    approval_ttl_seconds: int = 3_600
    reservation_seconds: int = 1_800
    max_affected_cells: int = 250_000
    lease_owner: str = "analysis-patch-service"

    def __post_init__(self) -> None:
        if min(
            self.approval_ttl_seconds,
            self.reservation_seconds,
            self.max_affected_cells,
        ) < 1:
            raise ValueError("patch service limits must be positive")


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """What the client learns immediately before it mutates anything."""

    proposal: PatchProposal
    assessment: ConflictAssessment
    rebased: PatchProposal | None = None

    @property
    def may_apply(self) -> bool:
        return self.assessment.resolution is ConflictResolution.PROCEED


class PlanLookup(Protocol):
    async def get_plan(
        self,
        *,
        user_id: str,
        run_id: str,
        plan_id: str,
    ) -> AnalysisPlan | None: ...


class ExecutionLookup(Protocol):
    async def get_by_key(
        self,
        *,
        user_id: str,
        execution_key: str,
    ) -> Any | None: ...


class PayloadWriterFactory(Protocol):
    def __call__(
        self,
        *,
        workspace_id: str,
        patch_id: str,
        patch_revision: int,
    ) -> Any: ...


class PayloadReader(Protocol):
    async def read_chunk(self, chunk: PayloadChunkReference) -> bytes: ...


LiveContext = dict[tuple[str, str], tuple[tuple[CellState, ...], ...]]

#: Phase order, so a relocation can wait from wherever the run already stands
#: without asking the state machine to move a phase backwards.
_PHASE_ORDER = (
    AnalysisRunPhase.CONTEXT_RESOLUTION,
    AnalysisRunPhase.EVIDENCE_PREPARATION,
    AnalysisRunPhase.REQUIREMENTS,
    AnalysisRunPhase.NORMALIZATION,
    AnalysisRunPhase.PLANNING,
    AnalysisRunPhase.PLAN_VALIDATION,
    AnalysisRunPhase.APPROVAL,
    AnalysisRunPhase.EXECUTION,
    AnalysisRunPhase.RESULT_VALIDATION,
    AnalysisRunPhase.PROPOSAL,
    AnalysisRunPhase.APPLICATION,
    AnalysisRunPhase.COMPLETED,
)


class PatchService:
    """The one entry point for every patch-lifecycle mutation."""

    def __init__(
        self,
        *,
        state_machine: AnalysisRunStateMachine,
        plans: PlanLookup,
        executions: ExecutionLookup,
        proposals: PatchProposalRepository,
        reservations: WriteReservationService,
        result_reader: ExecutionResultReader,
        payload_writers: PayloadWriterFactory | None = None,
        payload_reader: PayloadReader | None = None,
        config: PatchServiceConfig | None = None,
    ) -> None:
        self._state_machine = state_machine
        self._plans = plans
        self._executions = executions
        self._proposals = proposals
        self._reservations = reservations
        self._result_reader = result_reader
        self._payload_writers = payload_writers
        self._payload_reader = payload_reader
        self._config = config or PatchServiceConfig()

    # ---------------------------------------------------------------- 9.11.1

    async def request_context(
        self,
        *,
        run: AnalysisRun,
        plan: AnalysisPlan,
        execution_id: str,
        execution_key: str,
        output_rows: int,
        output_columns: int,
        worker_id: str,
        lease_attempt: int,
    ) -> None:
        """Park the run until the browser reports its live workbook.

        Emitted only once the output's exact dimensions are known — the whole
        point of the handshake is that the backend asks about the rectangle it
        will actually write, not a guess made before execution.
        """

        intent = workbook_write_intent(plan)
        if intent is None:  # pragma: no cover - callers check first
            raise PatchNotReadyError(
                "plan_has_no_workbook_write",
                "this plan does not write to a workbook",
            )
        target = intent.target
        await self._state_machine.transition(
            user_id=run.user_id,
            run_id=run.run_id,
            target_status=AnalysisRunStatus.WAITING,
            target_phase=AnalysisRunPhase.PROPOSAL,
            outcome=AnalysisRunOutcome.PATCH_CONTEXT_REQUIRED,
            event_type=AnalysisEventType.PATCH_CONTEXT_REQUIRED,
            payload={
                "workbook_id": target.workbook_id,
                "worksheet_id": target.worksheet_id,
                "source_range_a1": target.source_range_a1,
                "placement_policy": target.placement_policy.value,
                "minimum_column_gap": target.minimum_column_gap,
                "output_rows": output_rows,
                "output_columns": output_columns,
                "base_workbook_revision": target.base_workbook_revision,
            },
            deduplication_key=f"attempt-{lease_attempt}:patch-context-required",
            worker_id=worker_id,
            lease_attempt=lease_attempt,
            summary_updates={
                "current_execution_id": execution_id,
                "current_execution_key": execution_key,
                "patch_approval_status": RunApprovalStatus.PENDING,
            },
        )

    async def submit_context(
        self,
        *,
        user_id: str,
        run_id: str,
        context: WorkbookPatchContext,
        sheet_name_hint: str | None = None,
        trace_id: str | None = None,
    ) -> PatchProposal:
        """Place, reserve and compile against exactly this workbook view."""

        run = await self._state_machine.require_run(user_id=user_id, run_id=run_id)
        existing = await self._proposals.get_current(user_id=user_id, run_id=run_id)
        if (
            existing is not None
            and existing.context_hash == context.context_hash
            and existing.is_open
        ):
            # The same context posted twice is the same proposal, not a second
            # reservation on the same rectangle.
            return existing
        plan, execution = await self._require_inputs(run)
        intent = workbook_write_intent(plan)
        if intent is None:
            raise PatchNotReadyError(
                "plan_has_no_workbook_write",
                "this plan does not write to a workbook",
            )
        if context.workbook_id != intent.target.workbook_id:
            raise PatchServiceError(
                "workbook_mismatch",
                "the captured context is for a different workbook",
            )

        await self._record_event(
            user_id=user_id,
            run_id=run_id,
            event_type=AnalysisEventType.PATCH_CONTEXT_RECEIVED,
            phase=AnalysisRunPhase.PROPOSAL,
            payload={
                "context_hash": context.context_hash,
                "workbook_revision": context.workbook_revision,
                "sheet_count": len(context.sheets),
                "captured_cells": sum(
                    item.cell_count for item in context.candidates
                ),
            },
            deduplication_key=f"patch-context:{context.context_hash}",
            trace_id=trace_id,
        )

        result = await self._result_reader.read(execution)
        rows = result.row_count + 1
        columns = len(result.columns)
        decision = await self._place(
            run=run,
            intent=intent,
            plan=plan,
            context=context,
            output_rows=rows,
            output_columns=columns,
            sheet_name_hint=sheet_name_hint,
        )

        patch_id = str(uuid4())
        revision = 1
        reservation = await self._reserve(
            decision,
            run=run,
            intent=intent,
            context=context,
            patch_id=patch_id,
            patch_revision=revision,
        )

        grid = ResultGrid(
            columns=result.columns,
            records=result.rows,
            record_count=result.row_count,
        )
        try:
            compiled = await compile_patch(
                identity=PatchIdentity(
                    user_id=run.user_id,
                    workspace_id=run.workspace_id,
                    run_id=run.run_id,
                    plan_id=plan.plan_id,
                    plan_hash=plan.plan_hash,
                    execution_id=execution.execution_id,
                    workbook_id=context.workbook_id,
                    base_workbook_revision=context.workbook_revision,
                    patch_id=patch_id,
                    patch_revision=revision,
                    idempotency_key=context.idempotency_key,
                ),
                decision=decision,
                grid=grid,
                maximum_affected_cells=self._config.max_affected_cells,
                source_guard=_source_guard(intent, context),
                writer=self._writer_for(
                    workspace_id=run.workspace_id,
                    patch_id=patch_id,
                    patch_revision=revision,
                ),
            )
        except Exception:
            # The rectangle was claimed before the patch existed. If the patch
            # never materializes, give it straight back rather than leaving the
            # area locked until the lease expires.
            await self._reservations.release(
                user_id=run.user_id,
                reservation_id=reservation.reservation_id,
                reason="patch_compilation_failed",
            )
            raise
        proposal = PatchProposal(
            patch_id=patch_id,
            revision=revision,
            user_id=run.user_id,
            workspace_id=run.workspace_id,
            run_id=run.run_id,
            plan_id=plan.plan_id,
            execution_id=execution.execution_id,
            patch=compiled.patch.model_copy(
                update={"status": PatchStatus.AWAITING_APPROVAL}
            ),
            placement=_placement_summary(decision),
            preview=build_patch_preview(
                decision=decision,
                columns=result.columns,
                head=compiled.preview_head,
                privacy_mode=run.privacy_mode,
            ),
            context_hash=context.context_hash,
            reservation_id=reservation.reservation_id,
            status=PatchStatus.AWAITING_APPROVAL,
        )
        stored = await self._proposals.create(proposal)
        await self._announce(stored, run=run, trace_id=trace_id)
        return stored

    # ---------------------------------------------------------------- 9.12.1

    async def decide(
        self,
        *,
        user_id: str,
        run_id: str,
        command: PatchApprovalCommand,
        trace_id: str | None = None,
    ) -> PatchProposal:
        """Record the final human decision, bound to exact hashes."""

        now = utc_now()
        decided = await self._proposals.decide(
            user_id=user_id,
            run_id=run_id,
            command=command,
            decided_at=now,
            expires_at=now + timedelta(seconds=self._config.approval_ttl_seconds),
        )
        run = await self._state_machine.require_run(user_id=user_id, run_id=run_id)
        if command.decision == "approve":
            await self._transition_if_live(
                run,
                target_status=AnalysisRunStatus.WAITING,
                target_phase=AnalysisRunPhase.APPLICATION,
                outcome=AnalysisRunOutcome.AWAITING_APPLICATION,
                event_type=AnalysisEventType.PATCH_APPROVED,
                payload={
                    "patch_id": decided.patch_id,
                    "patch_revision": decided.revision,
                    "patch_hash": decided.patch.patch_hash,
                    "affected_cells": decided.patch.affected_cells,
                },
                deduplication_key=f"patch-approved:{command.decision_id}",
                trace_id=trace_id,
                summary_updates={
                    "patch_approval_status": RunApprovalStatus.APPROVED,
                },
            )
            return decided

        await self._release(decided, reason="patch_rejected")
        await self._transition_if_live(
            run,
            target_status=AnalysisRunStatus.SUCCEEDED,
            target_phase=AnalysisRunPhase.COMPLETED,
            outcome=AnalysisRunOutcome.REJECTED,
            event_type=AnalysisEventType.PATCH_REJECTED,
            payload={
                "patch_id": decided.patch_id,
                "patch_revision": decided.revision,
                "reason": (
                    command.rejection_reason.value
                    if command.rejection_reason is not None
                    else None
                ),
            },
            deduplication_key=f"patch-rejected:{command.decision_id}",
            trace_id=trace_id,
            summary_updates={
                "patch_approval_status": RunApprovalStatus.REJECTED,
            },
        )
        return decided

    # ---------------------------------------------------------------- 9.12.3

    async def preflight(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
        patch_revision: int,
        live: LiveContext,
        workbook_revision: int,
        workbook_present: bool = True,
        trace_id: str | None = None,
    ) -> PreflightResult:
        """Check the live workbook one last time, and act on the matrix."""

        proposal = await self._require_proposal(
            user_id=user_id,
            run_id=run_id,
            patch_id=patch_id,
            revision=patch_revision,
        )
        assessment = assess_conflict(
            proposal.patch,
            live=live,
            workbook_revision=workbook_revision,
            workbook_present=workbook_present,
        )
        if assessment.resolution is ConflictResolution.PROCEED:
            return PreflightResult(proposal=proposal, assessment=assessment)

        await self._record_event(
            user_id=user_id,
            run_id=run_id,
            event_type=AnalysisEventType.PATCH_CONFLICT_DETECTED,
            phase=AnalysisRunPhase.APPLICATION,
            payload={
                "patch_id": proposal.patch_id,
                "patch_revision": proposal.revision,
                "conflict": assessment.kind.value,
                "resolution": assessment.resolution.value,
                "issue_codes": sorted(
                    {issue.code for issue in assessment.issues}
                ),
            },
            deduplication_key=(
                f"patch-conflict:{proposal.patch_id}:{proposal.revision}:"
                f"{assessment.kind.value}:{workbook_revision}"
            ),
            trace_id=trace_id,
        )
        if assessment.resolution is ConflictResolution.REBASE:
            rebased = await self._rebase(
                proposal,
                workbook_revision=workbook_revision,
                trace_id=trace_id,
            )
            return PreflightResult(
                proposal=proposal,
                assessment=assessment,
                rebased=rebased,
            )
        if assessment.resolution is ConflictResolution.RELOCATE:
            await self._reopen_context(proposal, trace_id=trace_id)
        return PreflightResult(proposal=proposal, assessment=assessment)

    # ---------------------------------------------------------------- 9.12.4

    async def record_application(
        self,
        *,
        user_id: str,
        run_id: str,
        receipt: PatchApplicationReceipt,
        trace_id: str | None = None,
    ) -> PatchProposal:
        """Verify the client's receipt and, only then, complete the run."""

        proposal = await self._require_proposal(
            user_id=user_id,
            run_id=run_id,
            patch_id=receipt.patch_id,
            revision=receipt.patch_revision,
        )
        verdict = verify_receipt(receipt, proposal=proposal)
        if not verdict.accepted:
            await self._record_rejected_receipt(proposal, verdict, trace_id=trace_id)
            raise PatchServiceError(
                verdict.code or "patch_receipt_rejected",
                verdict.message or "the apply receipt was rejected",
            )
        if verdict.duplicate:
            # The edit already happened; the client only lost the answer.
            return proposal

        applied = await self._proposals.record_application(
            user_id=user_id,
            run_id=run_id,
            receipt=receipt,
        )
        await self._release(
            applied,
            status=ReservationStatus.APPLIED,
            reason="patch_applied",
        )
        run = await self._state_machine.require_run(user_id=user_id, run_id=run_id)
        await self._transition_if_live(
            run,
            target_status=AnalysisRunStatus.SUCCEEDED,
            target_phase=AnalysisRunPhase.COMPLETED,
            outcome=AnalysisRunOutcome.COMPLETED,
            event_type=AnalysisEventType.RUN_COMPLETED,
            payload={
                "patch_id": applied.patch_id,
                "patch_revision": applied.revision,
                "applied_revision": receipt.applied_revision,
                "affected_cells": applied.patch.affected_cells,
                "target_range_a1": applied.placement.target_range_a1,
            },
            deduplication_key=f"patch-applied:{receipt.application_id}",
            trace_id=trace_id,
            summary_updates={
                "applied_workbook_revision": receipt.applied_revision,
            },
        )
        return applied

    async def read_payload_chunk(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
        patch_revision: int,
        op_id: str,
        index: int,
    ) -> bytes:
        """Return one payload chunk, checked against the patch that owns it."""

        if self._payload_reader is None:
            raise PatchNotReadyError(
                "payload_storage_unavailable",
                "this deployment cannot serve patch payload chunks",
            )
        proposal = await self._require_proposal(
            user_id=user_id,
            run_id=run_id,
            patch_id=patch_id,
            revision=patch_revision,
        )
        chunk = _chunk_of(proposal, op_id=op_id, index=index)
        if chunk is None:
            raise PatchNotReadyError(
                "payload_chunk_not_found",
                f"operation '{op_id}' has no chunk {index}",
            )
        return await self._payload_reader.read_chunk(chunk)

    # ---------------------------------------------------------------- 9.12.6

    async def propose_undo(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
        patch_revision: int,
        workbook_revision: int,
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> PatchProposal:
        """Offer the stored inverse as a new, separately approved patch.

        The original application record is untouched. Undo is a second action
        with its own approval and its own receipt, not a rollback that quietly
        rewrites what happened.
        """

        applied = await self._require_proposal(
            user_id=user_id,
            run_id=run_id,
            patch_id=patch_id,
            revision=patch_revision,
        )
        undo_id = str(uuid4())
        patch = build_undo_patch(
            applied,
            patch_id=undo_id,
            idempotency_key=idempotency_key,
            workbook_revision=workbook_revision,
        ).model_copy(update={"status": PatchStatus.AWAITING_APPROVAL})
        proposal = PatchProposal(
            patch_id=undo_id,
            revision=1,
            user_id=applied.user_id,
            workspace_id=applied.workspace_id,
            run_id=applied.run_id,
            plan_id=applied.plan_id,
            execution_id=applied.execution_id,
            patch=patch,
            placement=applied.placement.model_copy(
                update={
                    "explanation": (
                        f"Undoes patch {applied.patch_id} in "
                        f"{applied.placement.target_range_a1}."
                    ),
                    "overwrites": True,
                }
            ),
            context_hash=applied.context_hash,
            status=PatchStatus.AWAITING_APPROVAL,
            undoes_patch_id=applied.patch_id,
        )
        stored = await self._proposals.create(proposal)
        await self._record_event(
            user_id=user_id,
            run_id=run_id,
            event_type=AnalysisEventType.PATCH_UNDO_PROPOSED,
            phase=AnalysisRunPhase.APPLICATION,
            payload={
                "patch_id": stored.patch_id,
                "undoes_patch_id": applied.patch_id,
                "affected_cells": stored.patch.affected_cells,
            },
            deduplication_key=f"patch-undo:{idempotency_key}",
            trace_id=trace_id,
        )
        return stored

    # ------------------------------------------------------------- internals

    async def _require_inputs(self, run: AnalysisRun) -> tuple[AnalysisPlan, Any]:
        if run.current_plan_id is None or run.current_execution_key is None:
            raise PatchNotReadyError(
                "patch_context_not_requested",
                "this run has no executed plan to build a patch from",
            )
        plan = await self._plans.get_plan(
            user_id=run.user_id,
            run_id=run.run_id,
            plan_id=run.current_plan_id,
        )
        if plan is None:
            raise PatchNotReadyError(
                "plan_not_found",
                "the plan this run executed is no longer available",
            )
        execution = await self._executions.get_by_key(
            user_id=run.user_id,
            execution_key=run.current_execution_key,
        )
        if execution is None or execution.artifacts is None:
            raise PatchNotReadyError(
                "result_not_published",
                "this run has no published result to write",
            )
        return plan, execution

    async def _place(
        self,
        *,
        run: AnalysisRun,
        intent: WorkbookWriteIntent,
        plan: AnalysisPlan,
        context: WorkbookPatchContext,
        output_rows: int,
        output_columns: int,
        sheet_name_hint: str | None,
    ) -> PlacementDecision:
        target = intent.target
        reserved = await self._reservations.occupied_rectangles(
            user_id=run.user_id,
            workbook_id=target.workbook_id,
            exclude_run_id=run.run_id,
        )
        request = PlacementRequest(
            workbook_id=target.workbook_id,
            policy=target.placement_policy,
            source_worksheet_id=target.worksheet_id,
            source_range_a1=target.source_range_a1,
            output_rows=output_rows,
            output_columns=output_columns,
            collision_policy=target.collision_policy,
            minimum_column_gap=target.minimum_column_gap,
            exact_target_range_a1=target.exact_target_range_a1,
            replacement_requested=intent.destructive,
            early_destructive_approval=(
                plan.approval.status is PlanApprovalStatus.APPROVED
            ),
            sheet_name_hint=sheet_name_hint or _sheet_hint(plan),
            identity=(run.run_id, plan.plan_hash),
        )
        try:
            return select_placement(request, context=context, reserved=reserved)
        except PlacementError as error:
            raise PatchServiceError(error.code.value, error.message) from error

    async def _reserve(
        self,
        decision: PlacementDecision,
        *,
        run: AnalysisRun,
        intent: WorkbookWriteIntent,
        context: WorkbookPatchContext,
        patch_id: str,
        patch_revision: int,
    ):
        try:
            return await self._reservations.reserve(
                decision,
                request=ReservationRequest(
                    user_id=run.user_id,
                    workspace_id=run.workspace_id,
                    workbook_id=intent.target.workbook_id,
                    run_id=run.run_id,
                    patch_id=patch_id,
                    patch_revision=patch_revision,
                    base_revision=context.workbook_revision,
                    lease_owner=self._config.lease_owner,
                ),
            )
        except SpatialReservationConflictError as error:
            raise PatchServiceError(
                "write_target_reservation_conflict",
                str(error),
            ) from error

    def _writer_for(
        self,
        *,
        workspace_id: str,
        patch_id: str,
        patch_revision: int,
    ) -> Any | None:
        if self._payload_writers is None:
            return None
        return self._payload_writers(
            workspace_id=workspace_id,
            patch_id=patch_id,
            patch_revision=patch_revision,
        )

    async def _announce(
        self,
        proposal: PatchProposal,
        *,
        run: AnalysisRun,
        trace_id: str | None,
    ) -> None:
        # A rebase re-proposes from the application phase, and a phase never
        # moves backwards, so the wait happens wherever the run already stands.
        phase = _at_least(run.phase, AnalysisRunPhase.PROPOSAL)
        await self._record_event(
            user_id=run.user_id,
            run_id=run.run_id,
            event_type=AnalysisEventType.PATCH_PROPOSED,
            phase=phase,
            payload={
                "patch_id": proposal.patch_id,
                "patch_revision": proposal.revision,
                "target_range_a1": proposal.placement.target_range_a1,
                "creates_sheet": proposal.placement.creates_sheet,
                "relocated": proposal.placement.relocated,
                "affected_cells": proposal.patch.affected_cells,
            },
            deduplication_key=(
                f"patch-proposed:{proposal.patch_id}:{proposal.revision}"
            ),
            trace_id=trace_id,
        )
        await self._transition_if_live(
            run,
            target_status=AnalysisRunStatus.WAITING,
            target_phase=phase,
            outcome=AnalysisRunOutcome.PATCH_READY,
            event_type=AnalysisEventType.PATCH_APPROVAL_REQUIRED,
            payload={
                "patch_id": proposal.patch_id,
                "patch_revision": proposal.revision,
                "patch_hash": proposal.patch.patch_hash,
                "target_range_a1": proposal.placement.target_range_a1,
                "affected_cells": proposal.patch.affected_cells,
                "reversible": proposal.patch.is_reversible,
            },
            deduplication_key=(
                f"patch-approval-required:{proposal.patch_id}:{proposal.revision}"
            ),
            trace_id=trace_id,
            summary_updates={
                "current_patch_id": proposal.patch_id,
                "current_patch_revision": proposal.revision,
                "current_patch_hash": proposal.patch.patch_hash,
                "patch_approval_status": RunApprovalStatus.PENDING,
            },
        )

    async def _rebase(
        self,
        proposal: PatchProposal,
        *,
        workbook_revision: int,
        trace_id: str | None,
    ) -> PatchProposal:
        patch = rebase_patch(
            proposal.patch,
            workbook_revision=workbook_revision,
        ).model_copy(update={"status": PatchStatus.AWAITING_APPROVAL})
        rebased = proposal.model_copy(
            update={
                "revision": patch.patch_revision,
                "patch": patch,
                "status": PatchStatus.AWAITING_APPROVAL,
                "approval": proposal.approval.model_copy(
                    update={
                        "status": PatchDecision.PENDING,
                        "binding": None,
                        "decision_id": None,
                        "decided_at": None,
                        "expires_at": None,
                        "rejection_reason": None,
                    }
                ),
                "supersedes_patch_id": proposal.patch_id,
                "version": 1,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
        )
        try:
            claimed = await self._reservations.reserve(
                _decision_from(rebased),
                request=ReservationRequest(
                    user_id=rebased.user_id,
                    workspace_id=rebased.workspace_id,
                    workbook_id=rebased.patch.workbook_id,
                    run_id=rebased.run_id,
                    patch_id=rebased.patch_id,
                    patch_revision=rebased.revision,
                    base_revision=workbook_revision,
                    lease_owner=self._config.lease_owner,
                ),
            )
        except SpatialReservationConflictError as error:  # pragma: no cover
            raise PatchServiceError(
                "write_target_reservation_conflict",
                str(error),
            ) from error
        # The claim is taken before the record is written, and the record points
        # at the claim it actually holds rather than the one it superseded.
        stored = await self._proposals.create(
            rebased.model_copy(update={"reservation_id": claimed.reservation_id})
        )
        run = await self._state_machine.require_run(
            user_id=stored.user_id,
            run_id=stored.run_id,
        )
        await self._record_event(
            user_id=stored.user_id,
            run_id=stored.run_id,
            event_type=AnalysisEventType.PATCH_REBASED,
            phase=AnalysisRunPhase.APPLICATION,
            payload={
                "patch_id": stored.patch_id,
                "patch_revision": stored.revision,
                "base_workbook_revision": workbook_revision,
            },
            deduplication_key=(
                f"patch-rebased:{stored.patch_id}:{stored.revision}"
            ),
            trace_id=trace_id,
        )
        await self._announce(stored, run=run, trace_id=trace_id)
        return stored

    async def _reopen_context(
        self,
        proposal: PatchProposal,
        *,
        trace_id: str | None,
    ) -> None:
        """Send the run back to the handshake so a new target can be chosen."""

        await self._release(proposal, reason="target_occupied")
        try:
            await self._proposals.mark_status(
                user_id=proposal.user_id,
                run_id=proposal.run_id,
                patch_id=proposal.patch_id,
                revision=proposal.revision,
                status=PatchStatus.SUPERSEDED,
            )
        except PatchConflictError:  # pragma: no cover - already closed
            pass
        run = await self._state_machine.require_run(
            user_id=proposal.user_id,
            run_id=proposal.run_id,
        )
        # The phase never moves backwards, so the run waits for a fresh context
        # from where it already is rather than pretending to re-enter proposal.
        await self._transition_if_live(
            run,
            target_status=AnalysisRunStatus.WAITING,
            target_phase=_at_least(run.phase, AnalysisRunPhase.APPLICATION),
            outcome=AnalysisRunOutcome.PATCH_CONTEXT_REQUIRED,
            event_type=AnalysisEventType.PATCH_CONTEXT_REQUIRED,
            payload={
                "superseded_patch_id": proposal.patch_id,
                "reason": "target_occupied",
                "workbook_id": proposal.patch.workbook_id,
                "worksheet_id": proposal.placement.worksheet_id,
            },
            deduplication_key=(
                f"patch-relocate:{proposal.patch_id}:{proposal.revision}"
            ),
            trace_id=trace_id,
            summary_updates={
                "patch_approval_status": RunApprovalStatus.PENDING,
            },
        )

    async def _record_rejected_receipt(
        self,
        proposal: PatchProposal,
        verdict: ReceiptVerdict,
        *,
        trace_id: str | None,
    ) -> None:
        await self._record_event(
            user_id=proposal.user_id,
            run_id=proposal.run_id,
            event_type=AnalysisEventType.PATCH_CONFLICT_DETECTED,
            phase=AnalysisRunPhase.APPLICATION,
            payload={
                "patch_id": proposal.patch_id,
                "patch_revision": proposal.revision,
                "conflict": (
                    ConflictKind.PARTIAL_STATE.value
                    if verdict.partial
                    else (verdict.code or "receipt_rejected")
                ),
                "reversible": proposal.patch.is_reversible,
            },
            deduplication_key=(
                f"patch-receipt-rejected:{proposal.patch_id}:"
                f"{proposal.revision}:{verdict.code}"
            ),
            trace_id=trace_id,
        )

    async def _release(
        self,
        proposal: PatchProposal,
        *,
        status: ReservationStatus = ReservationStatus.RELEASED,
        reason: str,
    ) -> None:
        if proposal.reservation_id is None:
            return
        await self._reservations.release(
            user_id=proposal.user_id,
            reservation_id=proposal.reservation_id,
            status=status,
            reason=reason,
        )

    async def _require_proposal(
        self,
        *,
        user_id: str,
        run_id: str,
        patch_id: str,
        revision: int,
    ) -> PatchProposal:
        proposal = await self._proposals.get(
            user_id=user_id,
            run_id=run_id,
            patch_id=patch_id,
            revision=revision,
        )
        if proposal is None:
            raise PatchNotReadyError(
                "patch_not_found",
                "no such patch for this run",
            )
        return proposal

    async def _record_event(
        self,
        *,
        user_id: str,
        run_id: str,
        **kwargs: Any,
    ) -> None:
        """Append a progress event, unless the run has already finished.

        A durable undo is proposed against a run that completed long ago, and a
        terminal run's event log is closed. The proposal itself is durable in
        the patch collection either way; only the activity entry is skipped.
        """

        run = await self._state_machine.require_run(
            user_id=user_id,
            run_id=run_id,
        )
        if run.status in TERMINAL_RUN_STATUSES:
            return
        await self._state_machine.record_event(
            user_id=user_id,
            run_id=run_id,
            **kwargs,
        )

    async def _transition_if_live(
        self,
        run: AnalysisRun,
        **kwargs: Any,
    ) -> None:
        """Move the run, unless it has already finished.

        A durable undo is proposed against a run that completed long ago. Its
        approval and application are real records, but there is no lifecycle
        left to advance, and forcing one would rewrite a terminal run.
        """

        if run.status in TERMINAL_RUN_STATUSES:
            return
        await self._state_machine.transition(
            user_id=run.user_id,
            run_id=run.run_id,
            **kwargs,
        )


def _at_least(
    current: AnalysisRunPhase,
    minimum: AnalysisRunPhase,
) -> AnalysisRunPhase:
    """Return whichever phase is further along; phases never move backwards."""

    return max(current, minimum, key=_PHASE_ORDER.index)


def workbook_write_intent(plan: AnalysisPlan) -> WorkbookWriteIntent | None:
    """Return the plan's workbook write intent, if it has one."""

    for intent in plan.write_intents:
        if isinstance(intent, WorkbookWriteIntent):
            return intent
    return None


def _sheet_hint(plan: AnalysisPlan) -> str:
    for artifact in plan.expected_artifacts:
        if artifact.kind in {"dataset", "workbook_patch"}:
            return artifact.title
    return plan.intent[:31]


def _source_guard(
    intent: WorkbookWriteIntent,
    context: WorkbookPatchContext,
) -> WorkbookGuard | None:
    """Guard the data the result was computed from, when it was captured."""

    source = context.source
    if source is None:
        return None
    return WorkbookGuard(
        worksheet_id=source.worksheet_id,
        range_a1=source.range_a1,
        expected_hash=source.content_hash,
        role="source",
    )


def _chunk_of(
    proposal: PatchProposal,
    *,
    op_id: str,
    index: int,
) -> PayloadChunkReference | None:
    """Return the requested chunk, from this patch and no other."""

    for operation in (*proposal.patch.operations, *proposal.patch.inverse_operations):
        if operation.op_id != op_id or not isinstance(
            operation.payload, ChunkedPayload
        ):
            continue
        for chunk in operation.payload.chunks:
            if chunk.index == index:
                return chunk
    return None


def _placement_summary(decision: PlacementDecision) -> PatchPlacementSummary:
    return PatchPlacementSummary(
        worksheet_id=decision.worksheet_id,
        worksheet_name=decision.worksheet_name,
        target_range_a1=decision.target_range_a1,
        policy=decision.policy_used.value,
        creates_sheet=decision.creates_sheet,
        overwrites=decision.overwrites,
        relocated=decision.relocated,
        explanation=decision.explanation,
        collision_codes=tuple(
            dict.fromkeys(item.kind.value for item in decision.collisions)
        ),
    )


def _decision_from(proposal: PatchProposal) -> PlacementDecision:
    """Rebuild the placement a stored proposal already decided.

    A rebase does not re-place anything — the rectangle is unchanged, and this
    only re-expresses it so the reservation service can claim it again under the
    new revision.
    """

    rect = Rect.from_a1(proposal.placement.target_range_a1)
    return PlacementDecision(
        policy_used=WorkbookPlacementPolicy(proposal.placement.policy),
        worksheet_id=proposal.placement.worksheet_id,
        worksheet_name=proposal.placement.worksheet_name,
        target_range_a1=proposal.placement.target_range_a1,
        target_rect=rect,
        creates_sheet=proposal.placement.creates_sheet,
        overwrites=proposal.placement.overwrites,
        relocated=proposal.placement.relocated,
        before_hash=proposal.patch.operations[0].expected_before_hash or "",
        explanation=proposal.placement.explanation,
    )


__all__ = [
    "PatchNotReadyError",
    "PatchService",
    "PatchServiceConfig",
    "PatchServiceError",
    "PreflightResult",
    "workbook_write_intent",
]
