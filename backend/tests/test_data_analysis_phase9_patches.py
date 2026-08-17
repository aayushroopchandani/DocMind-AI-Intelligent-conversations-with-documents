"""Phase 9.10 Workbook Patch Protocol v1.

The acceptance criteria these cover:

* backend and frontend calculate identical patch and cell hashes — the golden
  fixtures here are the same ones `npm run verify:cell-hash` asserts in the
  browser implementation;
* duplicate patch application is detectable before mutation;
* a patch never contains raw JavaScript or Univer commands;
* large payloads and inverse data remain outside MongoDB.
"""

from __future__ import annotations

import json
import unittest

from scripts.data_analysis_agent.runtime.models.workbook import WorkbookCellType
from scripts.data_analysis_agent.runtime.patches import (
    MAX_INLINE_CELLS,
    RESERVED_OPERATIONS,
    SUPPORTED_OPERATIONS,
    CellState,
    ChunkedPayload,
    InlinePayload,
    InverseNotAvailableError,
    PatchImpact,
    PatchOperation,
    PatchOperationType,
    PatchStatus,
    PayloadChunkReference,
    WorkbookGuard,
    WorkbookPatch,
    blank_range_hash,
    build_inverse,
    cell_hash,
    check_guards,
    compute_patch_hash,
    invert_operation,
    is_already_applied,
    operation_order,
    range_hash,
    summarize_impact,
    validate_patch,
)
from scripts.data_analysis_agent.runtime.patches.fixtures import (
    CELL_FIXTURES,
    RANGE_FIXTURES,
    expected_cell_hashes,
    expected_range_hashes,
)


_HASH = "a" * 64
_RUN_ID = "11111111-1111-4111-8111-111111111111"
_PLAN_ID = "22222222-2222-4222-8222-222222222222"


def text(value: str) -> CellState:
    return CellState(value=value, cell_type=WorkbookCellType.STRING)


def number(value: float) -> CellState:
    return CellState(value=value, cell_type=WorkbookCellType.NUMBER)


def grid(*rows: tuple[CellState, ...]) -> tuple[tuple[CellState, ...], ...]:
    return rows


RESULT_GRID = grid(
    (text("Region"), text("Revenue")),
    (text("North"), number(1234.5)),
)


def write_operation(**overrides) -> PatchOperation:
    values = {
        "op_id": "write_result",
        "operation_type": PatchOperationType.WRITE_RANGE,
        "worksheet_id": "sheet-1",
        "range_a1": "Sheet1!D1:E2",
        "expected_before_hash": blank_range_hash("Sheet1!D1:E2"),
        "expected_after_hash": range_hash("Sheet1!D1:E2", RESULT_GRID),
        "payload": InlinePayload(cells=RESULT_GRID),
        "affected_cells": 4,
    }
    values.update(overrides)
    return PatchOperation(**values)


def build_patch(**overrides) -> WorkbookPatch:
    operations = overrides.pop("operations", (write_operation(),))
    # Derive the inverse only when the caller did not supply one. `pop` with a
    # default would build it eagerly even when overridden.
    inverse = (
        overrides.pop("inverse_operations")
        if "inverse_operations" in overrides
        else build_inverse(operations)
    )
    values = {
        "patch_id": "patch-1",
        "patch_hash": "0" * 64,
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "run_id": _RUN_ID,
        "plan_id": _PLAN_ID,
        "plan_hash": _HASH,
        "execution_id": "exec-1",
        "workbook_id": "workbook-1",
        "base_workbook_revision": 12,
        "target_guards": (
            WorkbookGuard(
                worksheet_id="sheet-1",
                range_a1="Sheet1!D1:E2",
                expected_hash=blank_range_hash("Sheet1!D1:E2"),
                role="target",
            ),
        ),
        "operations": operations,
        "inverse_operations": inverse,
        "impact": summarize_impact(operations),
        "maximum_affected_cells": 1_000,
        "idempotency_key": "run-1:patch-1",
        "cell_hash_version": "1.0",
    }
    values.update(overrides)
    draft = WorkbookPatch(**values)
    return draft.model_copy(update={"patch_hash": compute_patch_hash(draft)})


# ------------------------------------------------------------ 9.10.4 hashes


class CellHashTests(unittest.TestCase):
    """The golden fixtures the browser implementation also asserts."""

    def test_every_cell_fixture_matches_its_golden_digest(self) -> None:
        expected = expected_cell_hashes()

        for fixture in CELL_FIXTURES:
            with self.subTest(fixture=fixture.name):
                self.assertEqual(cell_hash(fixture.cell), expected[fixture.name])

    def test_every_range_fixture_matches_its_golden_digest(self) -> None:
        expected = expected_range_hashes()

        for fixture in RANGE_FIXTURES:
            with self.subTest(fixture=fixture.name):
                self.assertEqual(
                    range_hash(fixture.range_a1, fixture.cells),
                    expected[fixture.name],
                )

    def test_a_blank_rectangle_has_a_canonical_hash(self) -> None:
        # A missing cell and an explicitly blank cell must not differ, or a
        # guard would depend on how the client enumerated the rectangle.
        explicit = range_hash(
            "Sheet1!A1:B2",
            grid((CellState(), CellState()), (CellState(), CellState())),
        )

        self.assertEqual(explicit, blank_range_hash("Sheet1!A1:B2"))

    def test_the_same_content_at_a_different_address_differs(self) -> None:
        self.assertNotEqual(
            range_hash("Sheet1!A1:B2", RESULT_GRID),
            range_hash("Sheet1!D1:E2", RESULT_GRID),
        )

    def test_an_empty_string_is_not_a_blank_cell(self) -> None:
        self.assertNotEqual(cell_hash(text("")), cell_hash(CellState()))
        self.assertFalse(text("").is_blank)

    def test_formatting_alone_does_not_occupy_a_cell(self) -> None:
        formatted = CellState(number_format="0.00")

        # A styled but empty target is still safe to write into.
        self.assertTrue(formatted.is_blank)
        self.assertNotEqual(cell_hash(formatted), cell_hash(CellState()))

    def test_a_grid_that_does_not_match_its_range_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            range_hash("Sheet1!A1:C3", RESULT_GRID)


# --------------------------------------------------------- 9.10.1 envelope


class PatchHashTests(unittest.TestCase):
    def test_the_hash_is_stable_for_identical_content(self) -> None:
        self.assertEqual(build_patch().patch_hash, build_patch().patch_hash)

    def test_a_different_workspace_produces_a_different_hash(self) -> None:
        # Tenant identity is inside the hash so an approval cannot be replayed
        # against another workspace.
        self.assertNotEqual(
            build_patch().patch_hash,
            build_patch(workspace_id="workspace-2").patch_hash,
        )

    def test_lifecycle_fields_do_not_change_the_hash(self) -> None:
        patch = build_patch()
        moved = patch.model_copy(update={"status": PatchStatus.AWAITING_APPROVAL})

        self.assertEqual(compute_patch_hash(moved), patch.patch_hash)

    def test_changing_an_operation_changes_the_hash(self) -> None:
        # Same content, different target: the guards move with the range.
        altered = write_operation(
            range_a1="Sheet1!F1:G2",
            expected_before_hash=blank_range_hash("Sheet1!F1:G2"),
            expected_after_hash=range_hash("Sheet1!F1:G2", RESULT_GRID),
        )

        self.assertNotEqual(
            build_patch().patch_hash,
            build_patch(operations=(altered,), target_guards=()).patch_hash,
        )


# ------------------------------------------------------- 9.10.2 operations


class OperationRegistryTests(unittest.TestCase):
    def test_unbuilt_operations_are_reserved_not_missing(self) -> None:
        for name in ("create_table", "attach_chart", "attach_image"):
            with self.subTest(name=name):
                self.assertIn(
                    PatchOperationType(name),
                    RESERVED_OPERATIONS,
                )

    def test_a_reserved_operation_fails_validation(self) -> None:
        reserved = PatchOperation(
            op_id="chart",
            operation_type=PatchOperationType.ATTACH_CHART,
            worksheet_id="sheet-1",
        )

        issues = validate_patch(
            build_patch(operations=(reserved,), inverse_operations=())
        )

        self.assertIn(
            "unsupported_patch_operation",
            {issue.code for issue in issues},
        )

    def test_delete_sheet_exists_only_for_the_undo_path(self) -> None:
        from scripts.data_analysis_agent.runtime.patches import PROPOSABLE_OPERATIONS

        self.assertIn(PatchOperationType.DELETE_SHEET, SUPPORTED_OPERATIONS)
        self.assertNotIn(PatchOperationType.DELETE_SHEET, PROPOSABLE_OPERATIONS)

    def test_an_operation_carries_no_executable_text(self) -> None:
        serialized = json.dumps(build_patch().model_dump(mode="json"))

        # A patch is data. Nothing in it can name a function to invoke.
        for forbidden in ("javascript:", "eval(", "function(", "=>", "univer."):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)

    def test_a_write_without_a_payload_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            PatchOperation(
                op_id="bad",
                operation_type=PatchOperationType.WRITE_RANGE,
                worksheet_id="sheet-1",
                range_a1="Sheet1!A1:B2",
            )

    def test_a_payload_must_match_its_target_range(self) -> None:
        with self.assertRaises(ValueError):
            write_operation(range_a1="Sheet1!A1:C3")

    def test_operations_are_ordered_by_their_dependencies(self) -> None:
        create = PatchOperation(
            op_id="create",
            operation_type=PatchOperationType.CREATE_SHEET,
            worksheet_id="sheet-2",
            sheet_name="Results",
        )
        write = write_operation(depends_on=("create",))
        patch = build_patch(
            operations=(write, create),
            inverse_operations=(),
            target_guards=(),
        )

        ordered = operation_order(patch)

        self.assertEqual([item.op_id for item in ordered], ["create", "write_result"])


class PayloadTests(unittest.TestCase):
    def test_a_small_grid_may_be_inline(self) -> None:
        self.assertEqual(InlinePayload(cells=RESULT_GRID).cell_count, 4)

    def test_an_oversized_grid_must_be_chunked(self) -> None:
        wide = tuple(
            tuple(number(float(index)) for index in range(21))
            for _ in range(21)
        )

        with self.assertRaises(ValueError):
            InlinePayload(cells=wide)

        self.assertGreater(21 * 21, MAX_INLINE_CELLS)

    def test_chunks_must_cover_every_row_exactly_once(self) -> None:
        def chunk(index: int, first: int, last: int) -> PayloadChunkReference:
            return PayloadChunkReference(
                index=index,
                first_row=first,
                last_row=last,
                byte_count=128,
                sha256=_HASH,
                object_key=f"patches/chunk-{index}",
            )

        ChunkedPayload(
            chunks=(chunk(0, 0, 49), chunk(1, 50, 99)),
            total_rows=100,
            total_columns=4,
        )

        with self.assertRaises(ValueError):
            # A gap between chunks would silently drop rows.
            ChunkedPayload(
                chunks=(chunk(0, 0, 49), chunk(1, 60, 99)),
                total_rows=100,
                total_columns=4,
            )

    def test_a_chunked_payload_keeps_bytes_out_of_mongodb(self) -> None:
        payload = ChunkedPayload(
            chunks=(
                PayloadChunkReference(
                    index=0,
                    first_row=0,
                    last_row=999,
                    byte_count=1_000_000,
                    sha256=_HASH,
                    object_key="patches/big",
                ),
            ),
            total_rows=1_000,
            total_columns=10,
        )

        serialized = json.dumps(payload.model_dump(mode="json"))
        self.assertIn("object_key", serialized)
        self.assertLess(len(serialized), 1_000)


# ---------------------------------------------------------- 9.10.5 inverse


class InverseTests(unittest.TestCase):
    def test_writing_into_verified_blank_cells_inverts_to_clearing(self) -> None:
        inverse = invert_operation(write_operation())

        self.assertEqual(inverse.operation_type, PatchOperationType.CLEAR_RANGE)
        self.assertEqual(inverse.range_a1, "Sheet1!D1:E2")

    def test_the_inverse_swaps_the_guards(self) -> None:
        operation = write_operation()

        inverse = invert_operation(operation)

        self.assertEqual(
            inverse.expected_before_hash,
            operation.expected_after_hash,
        )
        self.assertEqual(
            inverse.expected_after_hash,
            operation.expected_before_hash,
        )

    def test_overwriting_content_without_capture_is_refused(self) -> None:
        occupied = write_operation(
            expected_before_hash=range_hash("Sheet1!D1:E2", RESULT_GRID),
        )

        with self.assertRaises(InverseNotAvailableError):
            invert_operation(occupied)

    def test_overwriting_content_inverts_to_restoring_it(self) -> None:
        previous = grid(
            (text("Old A"), text("Old B")),
            (text("Old C"), number(9.5)),
        )
        occupied = write_operation(
            expected_before_hash=range_hash("Sheet1!D1:E2", previous),
        )

        inverse = invert_operation(occupied, before=previous)

        self.assertEqual(inverse.operation_type, PatchOperationType.WRITE_RANGE)
        self.assertEqual(inverse.payload.cells, previous)

    def test_creating_a_sheet_inverts_to_deleting_it(self) -> None:
        create = PatchOperation(
            op_id="create",
            operation_type=PatchOperationType.CREATE_SHEET,
            worksheet_id="sheet-2",
            sheet_name="Results",
        )

        inverse = invert_operation(create)

        self.assertEqual(inverse.operation_type, PatchOperationType.DELETE_SHEET)

    def test_undo_runs_in_reverse_application_order(self) -> None:
        create = PatchOperation(
            op_id="create",
            operation_type=PatchOperationType.CREATE_SHEET,
            worksheet_id="sheet-2",
            sheet_name="Results",
        )
        write = write_operation()

        inverse = build_inverse((create, write))

        self.assertEqual(
            [item.op_id for item in inverse],
            ["write_result__inverse", "create__inverse"],
        )

    def test_a_destructive_patch_without_an_inverse_is_refused(self) -> None:
        patch = build_patch(
            inverse_operations=(),
            impact=PatchImpact(
                cells_written=4,
                overwrites_existing_values=True,
            ),
        )

        issues = validate_patch(patch)

        self.assertIn("patch_not_reversible", {issue.code for issue in issues})


# ------------------------------------------------------------- application


class GuardTests(unittest.TestCase):
    def test_a_valid_patch_passes_its_guards(self) -> None:
        patch = build_patch()
        live = {
            ("sheet-1", "Sheet1!D1:E2"): grid(
                (CellState(), CellState()),
                (CellState(), CellState()),
            )
        }

        issues = check_guards(patch, live=live, workbook_revision=12)

        self.assertEqual(issues, ())

    def test_a_changed_target_fails_before_mutation(self) -> None:
        patch = build_patch()
        live = {("sheet-1", "Sheet1!D1:E2"): RESULT_GRID}

        issues = check_guards(patch, live=live, workbook_revision=12)

        self.assertIn("guard_hash_mismatch", {issue.code for issue in issues})

    def test_an_uncaptured_rectangle_is_never_assumed_empty(self) -> None:
        patch = build_patch()

        issues = check_guards(patch, live={}, workbook_revision=12)

        self.assertIn("guard_context_missing", {issue.code for issue in issues})

    def test_a_moved_workbook_revision_fails(self) -> None:
        patch = build_patch()
        live = {
            ("sheet-1", "Sheet1!D1:E2"): grid(
                (CellState(), CellState()),
                (CellState(), CellState()),
            )
        }

        issues = check_guards(patch, live=live, workbook_revision=13)

        self.assertIn(
            "workbook_revision_changed",
            {issue.code for issue in issues},
        )


class DuplicateApplicationTests(unittest.TestCase):
    def test_an_unapplied_patch_is_not_reported_as_applied(self) -> None:
        blank = grid((CellState(), CellState()), (CellState(), CellState()))

        self.assertFalse(
            is_already_applied(
                build_patch(),
                live={("sheet-1", "Sheet1!D1:E2"): blank},
            )
        )

    def test_an_applied_patch_is_detected_before_mutation(self) -> None:
        # The target already holds exactly what this patch would write.
        self.assertTrue(
            is_already_applied(
                build_patch(),
                live={("sheet-1", "Sheet1!D1:E2"): RESULT_GRID},
            )
        )

    def test_missing_context_is_not_treated_as_applied(self) -> None:
        self.assertFalse(is_already_applied(build_patch(), live={}))


class PatchIntegrityTests(unittest.TestCase):
    def test_a_well_formed_patch_validates_cleanly(self) -> None:
        self.assertEqual(validate_patch(build_patch()), ())

    def test_a_tampered_hash_is_detected(self) -> None:
        patch = build_patch().model_copy(update={"patch_hash": "b" * 64})

        issues = validate_patch(patch)

        self.assertIn("patch_hash_mismatch", {issue.code for issue in issues})

    def test_a_misdeclared_impact_is_detected(self) -> None:
        patch = build_patch(impact=PatchImpact(cells_written=99))

        issues = validate_patch(patch)

        self.assertIn("patch_impact_mismatch", {issue.code for issue in issues})

    def test_a_patch_beyond_its_declared_maximum_is_refused(self) -> None:
        # A fill touches cells without adding to the value-write impact, so the
        # model's impact check passes and only the validator catches the size.
        fill = PatchOperation(
            op_id="fill_margin",
            operation_type=PatchOperationType.FILL_FORMULA,
            worksheet_id="sheet-1",
            range_a1="Sheet1!F2:F101",
            formula="=IFERROR(IF(C2=0,0,(C2-D2)/C2),0)",
            affected_cells=100,
        )
        patch = build_patch(
            operations=(fill,),
            inverse_operations=(),
            target_guards=(),
            maximum_affected_cells=10,
        )

        issues = validate_patch(patch)

        self.assertIn("patch_too_large", {issue.code for issue in issues})

    def test_an_unknown_dependency_is_refused_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            build_patch(
                operations=(write_operation(depends_on=("nothing",)),),
                inverse_operations=(),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
