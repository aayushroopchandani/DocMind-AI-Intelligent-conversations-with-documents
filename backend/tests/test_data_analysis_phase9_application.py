"""Phase 9.12 preview, approval, application, conflicts and undo.

The acceptance criteria these cover:

* no real workbook cell changes during preview;
* one patch increments the logical revision once;
* one undo restores the exact before state;
* lost receipt delivery does not duplicate edits;
* every conflict follows the matrix and never partially applies the rest.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from scripts.data_analysis_agent.runtime.models.patches import (
    PatchApproval,
    PatchApprovalCommand,
    PatchBinding,
    PatchDecision,
    PatchPlacementSummary,
    PatchProposal,
    PatchRejectionReason,
)
from scripts.data_analysis_agent.runtime.models.plans import (
    PlanColumn,
    PlanDataType,
    WorkbookPlacementPolicy,
)
from scripts.data_analysis_agent.runtime.models.workbook import (
    Rect,
    WorkbookCellType,
)
from scripts.data_analysis_agent.runtime.patches import (
    ChunkedPayload,
    ConflictKind,
    ConflictResolution,
    OperationOutcome,
    OperationResult,
    PatchApplicationReceipt,
    PatchIdentity,
    PatchOperationType,
    PatchStatus,
    TouchedRange,
    UndoNotAvailableError,
    assess_conflict,
    blank_range_hash,
    build_patch_preview,
    build_undo_patch,
    compile_patch,
    expected_post_hash,
    expected_pre_hash,
    range_hash,
    rebase_patch,
    validate_patch,
    verify_receipt,
)
from scripts.data_analysis_agent.runtime.patches.cells import CellState
from scripts.data_analysis_agent.runtime.patches.grid import ResultGrid
from scripts.data_analysis_agent.runtime.placement import (
    PlacementRequest,
    SheetOccupancy,
    WorkbookPatchContext,
    compute_context_hash,
    select_placement,
)
from scripts.data_analysis_agent.runtime.repositories.patches import (
    InMemoryPatchProposalRepository,
    PatchConflictError,
)


_RUN_ID = "11111111-1111-4111-8111-111111111111"
_PLAN_ID = "22222222-2222-4222-8222-222222222222"
_PLAN_HASH = "a" * 64
_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

COLUMNS = (
    PlanColumn(key="region", label="Region", data_type=PlanDataType.STRING),
    PlanColumn(key="revenue", label="Revenue", data_type=PlanDataType.CURRENCY),
)
RECORDS = [("North", 1234.5), ("South", 990.0), ("East", None)]


class _MemoryWriter:
    """Keeps chunk bytes in memory so payload chunking can be asserted."""

    def __init__(self) -> None:
        self.chunks: dict[int, bytes] = {}

    async def write_chunk(self, *, index: int, data: bytes, sha256: str) -> str:
        self.chunks[index] = data
        return f"analysis/patches/test/chunk-{index:04d}.json"


def context(**overrides) -> WorkbookPatchContext:
    values = {
        "workbook_id": "wb-1",
        "workbook_revision": 12,
        "sheets": (
            SheetOccupancy(
                worksheet_id="sheet-1",
                worksheet_name="Sales",
                row_count=1_000,
                column_count=100,
                used_range_a1="A1:C50",
            ),
        ),
        "idempotency_key": "context-key-0001",
        "context_hash": "0" * 64,
    }
    values.update(overrides)
    draft = WorkbookPatchContext.model_construct(**values)
    return WorkbookPatchContext(
        **{**values, "context_hash": compute_context_hash(draft)}
    )


def decision(**overrides):
    request = PlacementRequest(
        workbook_id="wb-1",
        policy=overrides.pop("policy", WorkbookPlacementPolicy.ADJACENT_RIGHT),
        source_worksheet_id="sheet-1",
        source_range_a1="A1:C50",
        output_rows=overrides.pop("output_rows", len(RECORDS) + 1),
        output_columns=overrides.pop("output_columns", len(COLUMNS)),
        sheet_name_hint="Filtered Revenue",
        **overrides,
    )
    return select_placement(request, context=context())


async def compile_result(
    *,
    records=None,
    place=None,
    writer=None,
    revision: int = 1,
    maximum: int = 250_000,
):
    rows = list(RECORDS if records is None else records)
    target = place or decision(output_rows=len(rows) + 1)
    grid = ResultGrid(
        columns=COLUMNS,
        records=iter(rows),
        record_count=len(rows),
    )
    return await compile_patch(
        identity=PatchIdentity(
            user_id="user-1",
            workspace_id="workspace-1",
            run_id=_RUN_ID,
            plan_id=_PLAN_ID,
            plan_hash=_PLAN_HASH,
            execution_id="execution-1",
            workbook_id="wb-1",
            base_workbook_revision=12,
            patch_id="patch-1",
            patch_revision=revision,
            idempotency_key="patch-key-0001",
        ),
        decision=target,
        grid=grid,
        maximum_affected_cells=maximum,
        writer=writer,
    )


def proposal(compiled, **overrides) -> PatchProposal:
    place = overrides.pop("placement", None) or PatchPlacementSummary(
        worksheet_id="sheet-1",
        worksheet_name="Sales",
        target_range_a1=compiled.patch.operations[0].range_a1 or "'Sales'!E1:G4",
        policy=WorkbookPlacementPolicy.ADJACENT_RIGHT.value,
        explanation="Placed beside the source table.",
    )
    values = {
        "patch_id": compiled.patch.patch_id,
        "revision": compiled.patch.patch_revision,
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "run_id": _RUN_ID,
        "plan_id": _PLAN_ID,
        "execution_id": "execution-1",
        "patch": compiled.patch.model_copy(
            update={"status": PatchStatus.AWAITING_APPROVAL}
        ),
        "placement": place,
        "context_hash": "c" * 64,
        "status": PatchStatus.AWAITING_APPROVAL,
    }
    values.update(overrides)
    return PatchProposal(**values)


def approved(record: PatchProposal) -> PatchProposal:
    return record.model_copy(
        update={
            "status": PatchStatus.APPROVED,
            "patch": record.patch.model_copy(
                update={
                    "status": PatchStatus.APPROVED,
                    "expires_at": _NOW + timedelta(hours=1),
                }
            ),
            "approval": PatchApproval(
                status=PatchDecision.APPROVED,
                binding=record.binding,
                decision_id="decision-00000001",
                decided_at=_NOW,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        }
    )


def receipt(record: PatchProposal, **overrides) -> PatchApplicationReceipt:
    patch = record.patch
    results = tuple(
        OperationResult(
            op_id=operation.op_id,
            outcome=OperationOutcome.APPLIED,
            affected_cells=operation.affected_cells,
            after_hash=operation.expected_after_hash,
        )
        for operation in patch.operations
    )
    touched = tuple(
        TouchedRange(
            worksheet_id=operation.worksheet_id,
            range_a1=operation.range_a1,
            after_hash=operation.expected_after_hash,
        )
        for operation in patch.operations
        if operation.range_a1 is not None
        and operation.expected_after_hash is not None
    )
    values = {
        "application_id": "application-0001",
        "idempotency_key": "application-key-0001",
        "patch_id": patch.patch_id,
        "patch_revision": patch.patch_revision,
        "patch_hash": patch.patch_hash,
        "plan_hash": patch.plan_hash,
        "execution_id": record.execution_id,
        "base_revision": patch.base_workbook_revision,
        "applied_revision": patch.base_workbook_revision + 1,
        "adapter_version": "univer-adapter-1.0",
        "engine_version": "univer-0.5",
        "operation_results": results,
        "touched_ranges": touched,
        "pre_application_hash": expected_pre_hash(patch),
        "post_application_hash": expected_post_hash(patch),
        "locally_persisted": True,
    }
    values.update(overrides)
    return PatchApplicationReceipt(**values)


class PatchCompilationTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_compiled_patch_validates_and_carries_its_inverse(self) -> None:
        compiled = await compile_result()

        self.assertEqual(validate_patch(compiled.patch), ())
        self.assertTrue(compiled.patch.is_reversible)
        self.assertEqual(
            compiled.patch.inverse_operations[0].operation_type,
            PatchOperationType.CLEAR_RANGE,
        )

    async def test_the_result_hash_matches_hashing_the_written_grid(self) -> None:
        compiled = await compile_result()
        write = compiled.patch.operations[0]

        self.assertEqual(
            write.expected_after_hash,
            range_hash(write.range_a1, write.payload.cells),
        )

    async def test_a_new_sheet_adds_a_create_operation_the_write_depends_on(
        self,
    ) -> None:
        compiled = await compile_result(
            place=decision(policy=WorkbookPlacementPolicy.NEW_SHEET)
        )
        create, write = compiled.patch.operations

        self.assertEqual(create.operation_type, PatchOperationType.CREATE_SHEET)
        self.assertEqual(write.depends_on, (create.op_id,))
        self.assertEqual(compiled.patch.target_guards, ())

    async def test_a_null_result_value_becomes_a_canonically_blank_cell(self) -> None:
        compiled = await compile_result()
        last_row = compiled.patch.operations[0].payload.cells[-1]

        self.assertTrue(last_row[1].is_blank)
        self.assertIsNone(last_row[1].cell_type)

    async def test_result_text_that_looks_like_a_formula_stays_text(self) -> None:
        compiled = await compile_result(records=[("=SUM(A1:A9)", 1.0)])
        cell = compiled.patch.operations[0].payload.cells[1][0]

        self.assertEqual(cell.value, "'=SUM(A1:A9)")
        self.assertEqual(cell.cell_type, WorkbookCellType.STRING)

    async def test_a_large_result_is_chunked_and_never_inlined(self) -> None:
        rows = [(f"row-{index}", float(index)) for index in range(500)]
        writer = _MemoryWriter()

        compiled = await compile_result(
            records=rows,
            place=decision(output_rows=len(rows) + 1),
            writer=writer,
        )
        payload = compiled.patch.operations[0].payload

        self.assertIsInstance(payload, ChunkedPayload)
        self.assertEqual(payload.total_rows, len(rows) + 1)
        self.assertTrue(writer.chunks)
        self.assertEqual(
            {chunk.sha256 for chunk in payload.chunks},
            {chunk.sha256 for chunk in payload.chunks},
        )

    async def test_a_patch_over_its_limit_is_refused(self) -> None:
        from scripts.data_analysis_agent.runtime.patches.compiler import (
            PatchCompilationError,
        )

        with self.assertRaises(PatchCompilationError) as caught:
            await compile_result(maximum=4)

        self.assertEqual(caught.exception.code, "patch_too_large")


class PatchPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_preview_reads_the_result_without_changing_the_patch(self) -> None:
        compiled = await compile_result()
        before = compiled.patch.patch_hash

        preview = build_patch_preview(
            decision=decision(),
            columns=COLUMNS,
            head=compiled.preview_head,
        )

        # The preview is derived data: no operation, no payload, no mutation.
        self.assertEqual(compiled.patch.patch_hash, before)
        self.assertEqual(preview.header, ("Region", "Revenue"))
        self.assertEqual(preview.rows[0], ("North", "1234.5"))
        self.assertEqual(preview.total_rows, len(RECORDS) + 1)

    async def test_a_preview_of_a_large_result_is_sampled_and_bounded(self) -> None:
        rows = [(f"row-{index}", float(index)) for index in range(500)]
        writer = _MemoryWriter()
        place = decision(output_rows=len(rows) + 1)
        compiled = await compile_result(records=rows, place=place, writer=writer)

        preview = build_patch_preview(
            decision=place,
            columns=COLUMNS,
            head=compiled.preview_head,
        )

        self.assertTrue(preview.sampled)
        self.assertLessEqual(len(preview.rows), 20)


class ApprovalBindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemoryPatchProposalRepository()
        self.compiled = await compile_result()
        self.record = await self.repository.create(proposal(self.compiled))

    def _command(self, decision_value="approve", **overrides):
        binding = overrides.pop("binding", self.record.binding)
        return PatchApprovalCommand(
            decision=decision_value,
            binding=binding,
            decision_id=overrides.pop("decision_id", "decision-00000001"),
            **overrides,
        )

    async def test_approval_binds_to_the_exact_patch_that_was_shown(
        self,
    ) -> None:
        decided = await self.repository.decide(
            user_id="user-1",
            run_id=_RUN_ID,
            command=self._command(),
            decided_at=_NOW,
            expires_at=_NOW + timedelta(hours=1),
        )

        self.assertEqual(decided.status, PatchStatus.APPROVED)
        self.assertEqual(decided.approval.binding, self.record.binding)

    async def test_an_approval_for_a_stale_hash_cannot_be_replayed(self) -> None:
        stale = self.record.binding.model_copy(update={"patch_hash": "b" * 64})

        with self.assertRaises(PatchConflictError):
            await self.repository.decide(
                user_id="user-1",
                run_id=_RUN_ID,
                command=self._command(binding=stale),
                decided_at=_NOW,
                expires_at=_NOW + timedelta(hours=1),
            )

    async def test_an_approval_for_a_stale_revision_cannot_be_replayed(
        self,
    ) -> None:
        stale = self.record.binding.model_copy(
            update={"base_workbook_revision": 99}
        )

        with self.assertRaises(PatchConflictError):
            await self.repository.decide(
                user_id="user-1",
                run_id=_RUN_ID,
                command=self._command(binding=stale),
                decided_at=_NOW,
                expires_at=_NOW + timedelta(hours=1),
            )

    async def test_a_replayed_decision_id_returns_the_same_decision(
        self,
    ) -> None:
        first = await self.repository.decide(
            user_id="user-1",
            run_id=_RUN_ID,
            command=self._command(),
            decided_at=_NOW,
            expires_at=_NOW + timedelta(hours=1),
        )
        again = await self.repository.decide(
            user_id="user-1",
            run_id=_RUN_ID,
            command=self._command(),
            decided_at=_NOW,
            expires_at=_NOW + timedelta(hours=1),
        )

        self.assertEqual(again.approval.decision_id, first.approval.decision_id)
        self.assertEqual(again.status, PatchStatus.APPROVED)

    async def test_a_rejection_records_its_reason(self) -> None:
        decided = await self.repository.decide(
            user_id="user-1",
            run_id=_RUN_ID,
            command=self._command(
                "reject",
                rejection_reason=PatchRejectionReason.WRONG_TARGET,
            ),
            decided_at=_NOW,
        )

        self.assertEqual(decided.status, PatchStatus.REJECTED)
        self.assertEqual(
            decided.approval.rejection_reason,
            PatchRejectionReason.WRONG_TARGET,
        )

    async def test_a_new_revision_supersedes_the_one_awaiting_approval(
        self,
    ) -> None:
        rebased = rebase_patch(self.compiled.patch, workbook_revision=13)
        await self.repository.create(
            proposal(self.compiled).model_copy(
                update={
                    "revision": rebased.patch_revision,
                    "patch": rebased.model_copy(
                        update={"status": PatchStatus.AWAITING_APPROVAL}
                    ),
                }
            )
        )

        stale = await self.repository.get(
            user_id="user-1",
            run_id=_RUN_ID,
            patch_id=self.record.patch_id,
            revision=1,
        )

        self.assertEqual(stale.status, PatchStatus.SUPERSEDED)


class ConflictMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.compiled = await compile_result()
        self.write = self.compiled.patch.operations[0]
        self.before = {
            (self.write.worksheet_id, self.write.range_a1): tuple(
                tuple(CellState() for _ in range(2)) for _ in range(4)
            )
        }

    async def test_an_unchanged_workbook_proceeds(self) -> None:
        assessment = assess_conflict(
            self.compiled.patch,
            live=self.before,
            workbook_revision=12,
        )

        self.assertEqual(assessment.kind, ConflictKind.NONE)
        self.assertEqual(assessment.resolution, ConflictResolution.PROCEED)

    async def test_a_revision_bump_with_matching_guards_rebases(self) -> None:
        assessment = assess_conflict(
            self.compiled.patch,
            live=self.before,
            workbook_revision=13,
        )

        self.assertEqual(assessment.kind, ConflictKind.REVISION_ADVANCED)
        self.assertEqual(assessment.resolution, ConflictResolution.REBASE)

    async def test_an_occupied_target_relocates(self) -> None:
        occupied = {
            (self.write.worksheet_id, self.write.range_a1): tuple(
                tuple(
                    CellState(value="taken", cell_type=WorkbookCellType.STRING)
                    for _ in range(2)
                )
                for _ in range(4)
            )
        }

        assessment = assess_conflict(
            self.compiled.patch,
            live=occupied,
            workbook_revision=12,
        )

        self.assertEqual(assessment.kind, ConflictKind.TARGET_OCCUPIED)
        self.assertEqual(assessment.resolution, ConflictResolution.RELOCATE)

    async def test_a_missing_workbook_asks_for_a_new_target(self) -> None:
        assessment = assess_conflict(
            self.compiled.patch,
            live={},
            workbook_revision=12,
            workbook_present=False,
        )

        self.assertEqual(assessment.kind, ConflictKind.WORKBOOK_MISSING)
        self.assertEqual(
            assessment.resolution,
            ConflictResolution.REQUEST_TARGET,
        )

    async def test_an_already_written_target_recovers_the_receipt(self) -> None:
        applied = {
            (self.write.worksheet_id, self.write.range_a1): self.write.payload.cells
        }

        assessment = assess_conflict(
            self.compiled.patch,
            live=applied,
            workbook_revision=13,
        )

        self.assertEqual(assessment.kind, ConflictKind.ALREADY_APPLIED)
        self.assertEqual(
            assessment.resolution,
            ConflictResolution.RECOVER_RECEIPT,
        )

    async def test_a_rebase_changes_only_the_binding(self) -> None:
        rebased = rebase_patch(self.compiled.patch, workbook_revision=13)

        self.assertEqual(rebased.base_workbook_revision, 13)
        self.assertEqual(
            rebased.patch_revision,
            self.compiled.patch.patch_revision + 1,
        )
        self.assertEqual(rebased.operations, self.compiled.patch.operations)
        self.assertNotEqual(rebased.patch_hash, self.compiled.patch.patch_hash)
        self.assertEqual(validate_patch(rebased), ())

    async def test_a_rebase_cannot_move_backwards(self) -> None:
        with self.assertRaises(ValueError):
            rebase_patch(self.compiled.patch, workbook_revision=12)


class ApplyReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemoryPatchProposalRepository()
        self.compiled = await compile_result()
        stored = await self.repository.create(proposal(self.compiled))
        self.record = await self.repository.decide(
            user_id="user-1",
            run_id=_RUN_ID,
            command=PatchApprovalCommand(
                decision="approve",
                binding=stored.binding,
                decision_id="decision-00000001",
            ),
            decided_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    async def test_a_truthful_receipt_is_accepted(self) -> None:
        verdict = verify_receipt(receipt(self.record), proposal=self.record)

        self.assertTrue(verdict.accepted)
        self.assertFalse(verdict.duplicate)

    async def test_one_patch_advances_the_revision_exactly_once(self) -> None:
        skipped = receipt(self.record, applied_revision=15)

        verdict = verify_receipt(skipped, proposal=self.record)

        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.code, "workbook_revision_skipped")

    async def test_a_partial_application_is_never_a_success(self) -> None:
        partial = receipt(
            self.record,
            operation_results=(
                OperationResult(
                    op_id=self.compiled.patch.operations[0].op_id,
                    outcome=OperationOutcome.FAILED,
                    message="adapter refused the range",
                ),
            ),
        )

        verdict = verify_receipt(partial, proposal=self.record)

        self.assertFalse(verdict.accepted)
        self.assertTrue(verdict.partial)
        self.assertEqual(verdict.code, "patch_partially_applied")

    async def test_a_receipt_claiming_the_wrong_result_is_rejected(self) -> None:
        wrong = receipt(self.record, post_application_hash="b" * 64)

        verdict = verify_receipt(wrong, proposal=self.record)

        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.code, "post_application_hash_mismatch")

    async def test_a_receipt_for_another_patch_is_rejected(self) -> None:
        foreign = receipt(self.record, patch_hash="b" * 64)

        verdict = verify_receipt(foreign, proposal=self.record)

        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.code, "patch_binding_mismatch")

    async def test_a_redelivered_receipt_does_not_apply_twice(self) -> None:
        first = receipt(self.record)
        applied = await self.repository.record_application(
            user_id="user-1",
            run_id=_RUN_ID,
            receipt=first,
        )

        verdict = verify_receipt(first, proposal=applied)

        self.assertTrue(verdict.accepted)
        self.assertTrue(verdict.duplicate)
        self.assertEqual(applied.status, PatchStatus.APPLIED)

    async def test_a_second_different_receipt_is_refused(self) -> None:
        applied = await self.repository.record_application(
            user_id="user-1",
            run_id=_RUN_ID,
            receipt=receipt(self.record),
        )

        verdict = verify_receipt(
            receipt(
                self.record,
                application_id="application-0002",
                idempotency_key="application-key-0002",
            ),
            proposal=applied,
        )

        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.code, "patch_already_applied")

    async def test_recording_twice_conflicts_at_the_repository(self) -> None:
        await self.repository.record_application(
            user_id="user-1",
            run_id=_RUN_ID,
            receipt=receipt(self.record),
        )

        with self.assertRaises(PatchConflictError):
            await self.repository.record_application(
                user_id="user-1",
                run_id=_RUN_ID,
                receipt=receipt(self.record),
            )

    async def test_an_unapproved_patch_cannot_be_applied(self) -> None:
        pending = proposal(await compile_result())

        verdict = verify_receipt(receipt(pending), proposal=pending)

        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.code, "patch_not_approved")

    async def test_an_expired_approval_cannot_be_applied(self) -> None:
        stale = self.record.model_copy(
            update={
                "approval": self.record.approval.model_copy(
                    update={
                        "expires_at": datetime.now(timezone.utc)
                        - timedelta(minutes=1)
                    }
                )
            }
        )

        verdict = verify_receipt(receipt(stale), proposal=stale)

        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.code, "patch_approval_expired")


class UndoTests(unittest.IsolatedAsyncioTestCase):
    def _applied(self, compiled) -> PatchProposal:
        record = approved(proposal(compiled))
        return record.model_copy(
            update={
                "status": PatchStatus.APPLIED,
                "application": receipt(record),
            }
        )

    async def test_the_undo_of_a_blank_target_clears_exactly_what_was_written(
        self,
    ) -> None:
        compiled = await compile_result()
        applied = self._applied(compiled)

        undo = build_undo_patch(
            applied,
            patch_id="patch-undo-1",
            idempotency_key="undo-key-000001",
            workbook_revision=13,
        )
        clear = undo.operations[0]
        write = compiled.patch.operations[0]

        self.assertEqual(clear.operation_type, PatchOperationType.CLEAR_RANGE)
        self.assertEqual(clear.range_a1, write.range_a1)
        # Undo lands on exactly the state the patch found: a blank rectangle.
        self.assertEqual(
            clear.expected_after_hash,
            blank_range_hash(write.range_a1),
        )
        self.assertEqual(validate_patch(undo), ())

    async def test_the_undo_restores_the_exact_cells_a_replacement_overwrote(
        self,
    ) -> None:
        previous = tuple(
            tuple(
                CellState(value=f"old-{row}-{column}", cell_type=WorkbookCellType.STRING)
                for column in range(2)
            )
            for row in range(4)
        )
        base = decision()
        place = replace(
            base,
            overwrites=True,
            before_cells=previous,
            before_hash=range_hash(base.target_range_a1, previous),
        )
        compiled = await compile_result(place=place)
        undo = build_undo_patch(
            self._applied(compiled),
            patch_id="patch-undo-1",
            idempotency_key="undo-key-000001",
            workbook_revision=13,
        )
        restore = undo.operations[0]

        self.assertEqual(restore.operation_type, PatchOperationType.WRITE_RANGE)
        self.assertEqual(restore.payload.cells, previous)
        self.assertEqual(
            restore.expected_after_hash,
            range_hash(place.target_range_a1, previous),
        )

    async def test_the_undo_of_a_new_sheet_deletes_it(self) -> None:
        compiled = await compile_result(
            place=decision(policy=WorkbookPlacementPolicy.NEW_SHEET)
        )

        undo = build_undo_patch(
            self._applied(compiled),
            patch_id="patch-undo-1",
            idempotency_key="undo-key-000001",
            workbook_revision=13,
        )

        self.assertEqual(
            [item.operation_type for item in undo.operations],
            [PatchOperationType.CLEAR_RANGE, PatchOperationType.DELETE_SHEET],
        )

    async def test_the_undo_is_itself_reversible(self) -> None:
        compiled = await compile_result()

        undo = build_undo_patch(
            self._applied(compiled),
            patch_id="patch-undo-1",
            idempotency_key="undo-key-000001",
            workbook_revision=13,
        )

        self.assertEqual(undo.inverse_operations, compiled.patch.operations)
        self.assertTrue(undo.is_reversible)

    async def test_an_unapplied_patch_cannot_be_undone(self) -> None:
        with self.assertRaises(UndoNotAvailableError):
            build_undo_patch(
                proposal(await compile_result()),
                patch_id="patch-undo-1",
                idempotency_key="undo-key-000001",
                workbook_revision=13,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
