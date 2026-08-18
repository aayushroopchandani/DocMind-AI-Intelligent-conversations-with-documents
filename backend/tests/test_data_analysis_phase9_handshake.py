"""The patch handshake end to end (Phase 9.11.1 through 9.12.4).

One run, driven through every step the browser drives: the result exists, the
backend asks what the workbook looks like, the client answers, a patch is
placed, reserved, compiled and approved, and the run completes only when a
receipt arrives whose hashes the server itself produced.

Everything durable here is the real implementation — the real state machine over
the real run store, the real placement algorithm, the real compiler. Only the
two things that would need a network are faked: reading a published result, and
the plan/execution lookups.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from scripts.data_analysis_agent.runtime.execution.results.reader import ResultRows
from scripts.data_analysis_agent.runtime.models.events import AnalysisEventType
from scripts.data_analysis_agent.runtime.models.executions import (
    AnalysisExecution,
    ExecutionMetrics,
    ExecutionStatus,
    ResultArtifacts,
)
from scripts.data_analysis_agent.runtime.models.artifacts import (
    BlobProvider,
    BlobReference,
)
from scripts.data_analysis_agent.runtime.models.patches import (
    PatchApprovalCommand,
    PatchRejectionReason,
)
from scripts.data_analysis_agent.runtime.models.plans import (
    PlanColumn,
    PlanDataType,
    PlanDiagnostics,
    build_analysis_plan,
)
from scripts.data_analysis_agent.runtime.planning.validation import (
    derive_approval_policy,
)
from scripts.data_analysis_agent.runtime.models.runs import (
    AnalysisRunOutcome,
    AnalysisRunPhase,
    AnalysisRunStatus,
    RunApprovalStatus,
)
from scripts.data_analysis_agent.runtime.patches import (
    OperationOutcome,
    OperationResult,
    PatchApplicationReceipt,
    PatchStatus,
    TouchedRange,
    expected_post_hash,
    expected_pre_hash,
)
from scripts.data_analysis_agent.runtime.patches.cells import CellState
from scripts.data_analysis_agent.runtime.placement import (
    CapturedRange,
    SheetOccupancy,
    WorkbookPatchContext,
    WriteReservationService,
    compute_context_hash,
)
from scripts.data_analysis_agent.runtime.repositories.patches import (
    InMemoryPatchProposalRepository,
)
from scripts.data_analysis_agent.runtime.repositories.reservations import (
    InMemorySpatialReservationRepository,
)
from scripts.data_analysis_agent.runtime.repositories.runs import (
    MongoAnalysisRunStore,
)
from scripts.data_analysis_agent.runtime.services.patch_service import (
    PatchService,
    PatchServiceError,
)
from scripts.data_analysis_agent.runtime.services.state_machine import (
    AnalysisRunStateMachine,
)

from tests.test_data_analysis_phase8_planning import (
    _USER_ID,
    _Database,
    _context,
    _draft,
    _proposal,
    _run,
)


_WORKSPACE_ID = "workspace-1"
_WORKBOOK_ID = "workbook-1"
_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

RESULT_COLUMNS = (
    PlanColumn(key="region", label="Region", data_type=PlanDataType.STRING),
    PlanColumn(key="revenue", label="Revenue", data_type=PlanDataType.CURRENCY),
)
RESULT_RECORDS = [("North", 1234.5), ("South", 990.0), ("East", 4100.0)]


def _blob(name: str) -> BlobReference:
    return BlobReference(
        provider=BlobProvider.CLOUDINARY,
        object_key=f"analysis/results/test/{name}",
        content_type="application/octet-stream",
        filename=name,
        byte_count=64,
        sha256="d" * 64,
    )


class _Plans:
    def __init__(self, plan) -> None:
        self._plan = plan

    async def get_plan(self, *, user_id, run_id, plan_id):
        return self._plan


class _Executions:
    def __init__(self, execution) -> None:
        self._execution = execution

    async def get_by_key(self, *, user_id, execution_key):
        return self._execution


class _Results:
    """Hands back the published rows as a one-shot iterator, as the real reader
    does — a test that could iterate twice would not be testing the contract."""

    def __init__(self, records=None) -> None:
        self._records = list(RESULT_RECORDS if records is None else records)

    async def read(self, execution) -> ResultRows:
        return ResultRows(
            columns=RESULT_COLUMNS,
            row_count=len(self._records),
            rows=iter(self._records),
        )


def _context_document(
    *,
    revision: int = 12,
    used_range: str = "Sheet1!A1:B101",
    candidates: tuple[CapturedRange, ...] = (),
    key: str = "context-key-0001",
) -> WorkbookPatchContext:
    values = {
        "workbook_id": _WORKBOOK_ID,
        "workbook_revision": revision,
        "sheets": (
            SheetOccupancy(
                worksheet_id="sheet-1",
                worksheet_name="Sheet1",
                row_count=1_000,
                column_count=100,
                used_range_a1=used_range,
            ),
        ),
        "candidates": candidates,
        "idempotency_key": key,
        "context_hash": "0" * 64,
    }
    draft = WorkbookPatchContext.model_construct(**values)
    return WorkbookPatchContext(
        **{**values, "context_hash": compute_context_hash(draft)}
    )


class PatchHandshakeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.database = _Database()
        self.store = MongoAnalysisRunStore(self.database)
        self.state_machine = AnalysisRunStateMachine(self.store)
        self.proposals = InMemoryPatchProposalRepository()
        self.reservation_repository = InMemorySpatialReservationRepository()

        self.context = _context()
        proposal = _proposal()
        draft = _draft(self.context, proposal)
        self.plan = build_analysis_plan(
            draft=draft,
            user_id=self.context.user_id,
            workspace_id=self.context.workspace_id,
            revision=1,
            approval_policy=derive_approval_policy(
                draft=draft,
                context=self.context,
            ),
            diagnostics=PlanDiagnostics(generation_attempt=1, repair_count=0),
            model="test-planner",
            now=_NOW,
        )
        self.execution = AnalysisExecution(
            execution_id="execution-1",
            execution_key="e" * 64,
            user_id=_USER_ID,
            workspace_id=_WORKSPACE_ID,
            run_id=self.context.run_id,
            plan_id=self.plan.plan_id,
            plan_hash=self.plan.plan_hash,
            recipe_hash="f" * 64,
            engine_version="polars-1.0",
            semantics_version="1.0",
            status=ExecutionStatus.SUCCEEDED,
            result_content_hash="a" * 64,
            result_columns=RESULT_COLUMNS,
            artifacts=ResultArtifacts(
                rows=_blob("result.csv.gz"),
                schema_manifest=_blob("schema.json"),
                lineage=_blob("lineage.json"),
                preview=_blob("preview.json"),
            ),
            metrics=ExecutionMetrics(
                output_rows=len(RESULT_RECORDS),
                output_columns=len(RESULT_COLUMNS),
            ),
            started_at=_NOW,
            finished_at=_NOW,
        )
        self.service = PatchService(
            state_machine=self.state_machine,
            plans=_Plans(self.plan),
            executions=_Executions(self.execution),
            proposals=self.proposals,
            reservations=WriteReservationService(
                self.reservation_repository,
                clock=lambda: _NOW,
            ),
            result_reader=_Results(),
        )
        self.run = await self._started_run()

    async def _started_run(self):
        run = _run(self.context)
        await self.state_machine.create_run(run=run)
        await self.state_machine.transition(
            user_id=run.user_id,
            run_id=run.run_id,
            target_status=AnalysisRunStatus.ACTIVE,
            target_phase=AnalysisRunPhase.EXECUTION,
            outcome=None,
            event_type=AnalysisEventType.EXECUTION_STARTED,
            payload={"plan_id": self.plan.plan_id},
            summary_updates={
                "current_plan_id": self.plan.plan_id,
                "current_plan_revision": 1,
                "current_plan_hash": self.plan.plan_hash,
                "plan_approval_status": RunApprovalStatus.NOT_REQUIRED,
            },
        )
        return await self.state_machine.require_run(
            user_id=run.user_id,
            run_id=run.run_id,
        )

    async def _request_context(self):
        await self.service.request_context(
            run=self.run,
            plan=self.plan,
            execution_id=self.execution.execution_id,
            execution_key=self.execution.execution_key,
            output_rows=len(RESULT_RECORDS) + 1,
            output_columns=len(RESULT_COLUMNS),
            worker_id="worker-1",
            lease_attempt=1,
        )
        return await self.state_machine.require_run(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
        )

    async def _propose(self, **overrides):
        await self._request_context()
        return await self.service.submit_context(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
            context=_context_document(**overrides),
        )

    def _receipt(self, proposal, **overrides) -> PatchApplicationReceipt:
        patch = proposal.patch
        values = {
            "application_id": "application-0001",
            "idempotency_key": "application-key-0001",
            "patch_id": patch.patch_id,
            "patch_revision": patch.patch_revision,
            "patch_hash": patch.patch_hash,
            "plan_hash": patch.plan_hash,
            "execution_id": proposal.execution_id,
            "base_revision": patch.base_workbook_revision,
            "applied_revision": patch.base_workbook_revision + 1,
            "adapter_version": "univer-adapter-1.0",
            "engine_version": "univer-0.5",
            "operation_results": tuple(
                OperationResult(
                    op_id=operation.op_id,
                    outcome=OperationOutcome.APPLIED,
                    affected_cells=operation.affected_cells,
                    after_hash=operation.expected_after_hash,
                )
                for operation in patch.operations
            ),
            "touched_ranges": tuple(
                TouchedRange(
                    worksheet_id=operation.worksheet_id,
                    range_a1=operation.range_a1,
                    after_hash=operation.expected_after_hash,
                )
                for operation in patch.operations
                if operation.range_a1 is not None
                and operation.expected_after_hash is not None
            ),
            "pre_application_hash": expected_pre_hash(patch),
            "post_application_hash": expected_post_hash(patch),
            "locally_persisted": True,
        }
        values.update(overrides)
        return PatchApplicationReceipt(**values)

    async def _approve(self, proposal):
        return await self.service.decide(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
            command=PatchApprovalCommand(
                decision="approve",
                binding=proposal.binding,
                decision_id="decision-00000001",
            ),
        )

    async def test_a_workbook_run_waits_for_context_instead_of_completing(
        self,
    ) -> None:
        run = await self._request_context()

        self.assertEqual(run.status, AnalysisRunStatus.WAITING)
        self.assertEqual(run.phase, AnalysisRunPhase.PROPOSAL)
        self.assertEqual(run.outcome, AnalysisRunOutcome.PATCH_CONTEXT_REQUIRED)
        self.assertEqual(run.current_execution_key, self.execution.execution_key)

    async def test_submitting_context_places_reserves_and_proposes(self) -> None:
        proposal = await self._propose()
        run = await self.state_machine.require_run(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
        )
        active = await self.reservation_repository.list_active(
            user_id=self.run.user_id,
            workbook_id=_WORKBOOK_ID,
            now=_NOW,
        )

        self.assertEqual(proposal.status, PatchStatus.AWAITING_APPROVAL)
        self.assertEqual(proposal.placement.target_range_a1, "'Sheet1'!D1:E4")
        self.assertEqual(run.outcome, AnalysisRunOutcome.PATCH_READY)
        self.assertEqual(run.current_patch_hash, proposal.patch.patch_hash)
        self.assertEqual(run.patch_approval_status, RunApprovalStatus.PENDING)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].patch_id, proposal.patch_id)

    async def test_the_proposal_carries_a_preview_but_no_raw_commands(
        self,
    ) -> None:
        proposal = await self._propose()

        self.assertIsNotNone(proposal.preview)
        self.assertEqual(proposal.preview.header, ("Region", "Revenue"))
        # Nothing in a patch names a function to call.
        serialized = proposal.patch.model_dump_json()
        for forbidden in ("javascript", "univer", "eval(", "function"):
            self.assertNotIn(forbidden, serialized.casefold())

    async def test_resubmitting_the_same_context_returns_the_same_proposal(
        self,
    ) -> None:
        first = await self._propose()
        again = await self.service.submit_context(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
            context=_context_document(),
        )
        active = await self.reservation_repository.list_active(
            user_id=self.run.user_id,
            workbook_id=_WORKBOOK_ID,
            now=_NOW,
        )

        self.assertEqual(again.patch_id, first.patch_id)
        self.assertEqual(len(active), 1)

    async def test_an_occupied_target_is_relocated_never_overwritten(
        self,
    ) -> None:
        occupied = CapturedRange(
            worksheet_id="sheet-1",
            range_a1="D1:E4",
            cells=tuple(
                tuple(CellState(value="taken") for _ in range(2))
                for _ in range(4)
            ),
        )

        proposal = await self._propose(
            used_range="Sheet1!A1:J101",
            candidates=(occupied,),
        )

        self.assertTrue(proposal.placement.creates_sheet)
        self.assertTrue(proposal.placement.relocated)
        self.assertFalse(proposal.placement.overwrites)

    async def test_a_context_for_another_workbook_is_refused(self) -> None:
        await self._request_context()
        wrong = _context_document().model_copy(
            update={"workbook_id": "workbook-other"}
        )
        wrong = wrong.model_copy(
            update={"context_hash": compute_context_hash(wrong)}
        )

        with self.assertRaises(PatchServiceError) as caught:
            await self.service.submit_context(
                user_id=self.run.user_id,
                run_id=self.run.run_id,
                context=wrong,
            )

        self.assertEqual(caught.exception.code, "workbook_mismatch")

    async def test_approval_parks_the_run_for_application(self) -> None:
        proposal = await self._propose()

        approved = await self._approve(proposal)
        run = await self.state_machine.require_run(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
        )

        self.assertEqual(approved.status, PatchStatus.APPROVED)
        self.assertEqual(run.status, AnalysisRunStatus.WAITING)
        self.assertEqual(run.phase, AnalysisRunPhase.APPLICATION)
        self.assertEqual(run.outcome, AnalysisRunOutcome.AWAITING_APPLICATION)
        self.assertEqual(run.patch_approval_status, RunApprovalStatus.APPROVED)

    async def test_rejection_ends_the_run_and_frees_the_rectangle(self) -> None:
        proposal = await self._propose()

        await self.service.decide(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
            command=PatchApprovalCommand(
                decision="reject",
                binding=proposal.binding,
                decision_id="decision-00000002",
                rejection_reason=PatchRejectionReason.WRONG_TARGET,
            ),
        )
        run = await self.state_machine.require_run(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
        )
        active = await self.reservation_repository.list_active(
            user_id=self.run.user_id,
            workbook_id=_WORKBOOK_ID,
            now=_NOW,
        )

        self.assertEqual(run.status, AnalysisRunStatus.SUCCEEDED)
        self.assertEqual(run.outcome, AnalysisRunOutcome.REJECTED)
        self.assertEqual(run.patch_approval_status, RunApprovalStatus.REJECTED)
        self.assertEqual(active, ())

    async def test_a_verified_receipt_completes_the_run_once(self) -> None:
        proposal = await self._approve(await self._propose())

        applied = await self.service.record_application(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
            receipt=self._receipt(proposal),
        )
        run = await self.state_machine.require_run(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
        )

        self.assertEqual(applied.status, PatchStatus.APPLIED)
        self.assertEqual(run.status, AnalysisRunStatus.SUCCEEDED)
        self.assertEqual(run.outcome, AnalysisRunOutcome.COMPLETED)
        self.assertEqual(
            run.applied_workbook_revision,
            proposal.patch.base_workbook_revision + 1,
        )

    async def test_a_redelivered_receipt_does_not_apply_again(self) -> None:
        proposal = await self._approve(await self._propose())
        receipt = self._receipt(proposal)
        first = await self.service.record_application(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
            receipt=receipt,
        )

        again = await self.service.record_application(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
            receipt=receipt,
        )

        self.assertEqual(again.application.application_id, "application-0001")
        self.assertEqual(again.version, first.version)

    async def test_a_partial_receipt_does_not_complete_the_run(self) -> None:
        proposal = await self._approve(await self._propose())
        partial = self._receipt(
            proposal,
            operation_results=(
                OperationResult(
                    op_id=proposal.patch.operations[0].op_id,
                    outcome=OperationOutcome.FAILED,
                    message="the adapter refused the range",
                ),
            ),
        )

        with self.assertRaises(PatchServiceError) as caught:
            await self.service.record_application(
                user_id=self.run.user_id,
                run_id=self.run.run_id,
                receipt=partial,
            )
        run = await self.state_machine.require_run(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
        )

        self.assertEqual(caught.exception.code, "patch_partially_applied")
        self.assertEqual(run.status, AnalysisRunStatus.WAITING)
        self.assertIsNone(run.applied_workbook_revision)

    async def test_a_revision_bump_rebases_into_a_new_approvable_patch(
        self,
    ) -> None:
        proposal = await self._approve(await self._propose())
        write = proposal.patch.operations[0]
        blank = {
            (write.worksheet_id, write.range_a1): tuple(
                tuple(CellState() for _ in range(2)) for _ in range(4)
            )
        }

        outcome = await self.service.preflight(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
            patch_id=proposal.patch_id,
            patch_revision=proposal.revision,
            live=blank,
            workbook_revision=13,
        )

        self.assertFalse(outcome.may_apply)
        self.assertEqual(outcome.assessment.resolution.value, "rebase")
        self.assertIsNotNone(outcome.rebased)
        self.assertEqual(outcome.rebased.revision, proposal.revision + 1)
        self.assertEqual(outcome.rebased.status, PatchStatus.AWAITING_APPROVAL)
        self.assertEqual(outcome.rebased.supersedes_patch_id, proposal.patch_id)

    async def test_an_unchanged_workbook_passes_preflight(self) -> None:
        proposal = await self._approve(await self._propose())
        write = proposal.patch.operations[0]
        blank = {
            (write.worksheet_id, write.range_a1): tuple(
                tuple(CellState() for _ in range(2)) for _ in range(4)
            )
        }

        outcome = await self.service.preflight(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
            patch_id=proposal.patch_id,
            patch_revision=proposal.revision,
            live=blank,
            workbook_revision=12,
        )

        self.assertTrue(outcome.may_apply)
        self.assertIsNone(outcome.rebased)

    async def test_an_applied_patch_can_be_undone_as_a_new_action(self) -> None:
        proposal = await self._approve(await self._propose())
        applied = await self.service.record_application(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
            receipt=self._receipt(proposal),
        )

        undo = await self.service.propose_undo(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
            patch_id=applied.patch_id,
            patch_revision=applied.revision,
            workbook_revision=13,
            idempotency_key="undo-key-000001",
        )
        original = await self.proposals.get(
            user_id=self.run.user_id,
            run_id=self.run.run_id,
            patch_id=applied.patch_id,
            revision=applied.revision,
        )

        self.assertEqual(undo.undoes_patch_id, applied.patch_id)
        self.assertEqual(undo.status, PatchStatus.AWAITING_APPROVAL)
        # The original application record stands; undo is a second action.
        self.assertEqual(original.status, PatchStatus.APPLIED)
        self.assertIsNotNone(original.application)

    async def test_a_read_only_plan_never_enters_the_handshake(self) -> None:
        from scripts.data_analysis_agent.runtime.services.patch_service import (
            workbook_write_intent,
        )

        read_only = build_analysis_plan(
            draft=_draft(self.context, _proposal(with_write=False)),
            user_id=self.context.user_id,
            workspace_id=self.context.workspace_id,
            revision=1,
            approval_policy=derive_approval_policy(
                draft=_draft(self.context, _proposal(with_write=False)),
                context=self.context,
            ),
            diagnostics=PlanDiagnostics(generation_attempt=1, repair_count=0),
            model="test-planner",
            now=_NOW,
        )

        self.assertIsNone(workbook_write_intent(read_only))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
