"""Phase 9.11 post-execution placement and write reservations.

The acceptance criteria these cover:

* a full output rectangle is checked after output size is known;
* two concurrent patches cannot reserve overlapping rectangles;
* non-overlapping patches on one sheet may proceed;
* a collision causes relocation or a new-sheet proposal, never silent overwrite.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from scripts.data_analysis_agent.runtime.models.plans import (
    WorkbookCollisionPolicy,
    WorkbookPlacementPolicy,
)
from scripts.data_analysis_agent.runtime.models.reservations import (
    ReservationStatus,
    SpatialReservation,
)
from scripts.data_analysis_agent.runtime.models.workbook import (
    Rect,
    WorkbookCellType,
)
from scripts.data_analysis_agent.runtime.patches.cells import CellState
from scripts.data_analysis_agent.runtime.placement import (
    CapturedRange,
    CollisionKind,
    PlacementError,
    PlacementFailure,
    PlacementRequest,
    ReservationRequest,
    SheetOccupancy,
    WorkbookPatchContext,
    WriteReservationService,
    compute_context_hash,
    deterministic_worksheet_id,
    inspect_rectangle,
    sanitize_sheet_name,
    select_placement,
    unique_sheet_name,
)
from scripts.data_analysis_agent.runtime.repositories.reservations import (
    InMemorySpatialReservationRepository,
    SpatialReservationConflictError,
)


_RUN_A = "11111111-1111-4111-8111-111111111111"
_RUN_B = "22222222-2222-4222-8222-222222222222"
_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def text(value: str) -> CellState:
    return CellState(value=value, cell_type=WorkbookCellType.STRING)


def sheet(**overrides) -> SheetOccupancy:
    values = {
        "worksheet_id": "sheet-1",
        "worksheet_name": "Sales",
        "row_count": 1_000,
        "column_count": 100,
        "used_range_a1": "A1:C50",
    }
    values.update(overrides)
    return SheetOccupancy(**values)


def context(**overrides) -> WorkbookPatchContext:
    values = {
        "workbook_id": "wb-1",
        "workbook_revision": 12,
        "sheets": (sheet(),),
        "idempotency_key": "context-key-0001",
        "context_hash": "0" * 64,
    }
    values.update(overrides)
    draft = WorkbookPatchContext.model_construct(**values)
    return WorkbookPatchContext(
        **{**values, "context_hash": compute_context_hash(draft)}
    )


def request(**overrides) -> PlacementRequest:
    values = {
        "workbook_id": "wb-1",
        "policy": WorkbookPlacementPolicy.ADJACENT_RIGHT,
        "source_worksheet_id": "sheet-1",
        "source_range_a1": "A1:C50",
        "output_rows": 25,
        "output_columns": 3,
        "sheet_name_hint": "Filtered Revenue",
    }
    values.update(overrides)
    return PlacementRequest(**values)


def occupied_capture(range_a1: str, *, at: tuple[int, int] = (0, 0)) -> CapturedRange:
    rect = Rect.from_a1(range_a1)
    return CapturedRange(
        worksheet_id="sheet-1",
        range_a1=range_a1,
        cells=tuple(
            tuple(
                text("existing") if (row, column) == at else CellState()
                for column in range(rect.columns)
            )
            for row in range(rect.rows)
        ),
    )


def reservation(
    *,
    run_id: str = _RUN_A,
    patch_id: str = "patch-a",
    revision: int = 1,
    rect: Rect | None = None,
    worksheet_id: str = "sheet-1",
) -> SpatialReservation:
    return SpatialReservation.for_rect(
        rect or Rect.from_a1("E1:G25"),
        reservation_id=str(uuid4()),
        user_id="user-1",
        workspace_id="workspace-1",
        workbook_id="wb-1",
        worksheet_id=worksheet_id,
        run_id=run_id,
        patch_id=patch_id,
        patch_revision=revision,
        base_revision=12,
        lease_owner="test",
        expires_at=_NOW + timedelta(minutes=30),
    ).model_copy(update={"created_at": _NOW, "updated_at": _NOW})


class AdjacentPlacementTests(unittest.TestCase):
    def test_the_result_lands_two_columns_past_the_source(self) -> None:
        decision = select_placement(request(), context=context())

        self.assertEqual(decision.target_range_a1, "'Sales'!E1:G25")
        self.assertFalse(decision.creates_sheet)
        self.assertFalse(decision.overwrites)

    def test_the_whole_output_rectangle_is_checked_not_just_its_corner(
        self,
    ) -> None:
        # The blocking cell is in the last row and last column of the target,
        # which a corner-only check would miss entirely.
        blocked = context(
            sheets=(sheet(used_range_a1="A1:J50"),),
            candidates=(occupied_capture("E1:G25", at=(24, 2)),),
        )

        decision = select_placement(request(), context=blocked)

        self.assertTrue(decision.creates_sheet)
        self.assertIn(
            CollisionKind.VALUES,
            {item.kind for item in decision.collisions},
        )

    def test_an_occupied_target_relocates_instead_of_overwriting(self) -> None:
        blocked = context(
            sheets=(sheet(used_range_a1="A1:J50"),),
            candidates=(occupied_capture("E1:G25"),),
        )

        decision = select_placement(request(), context=blocked)

        self.assertTrue(decision.creates_sheet)
        self.assertTrue(decision.relocated)
        self.assertFalse(decision.overwrites)
        self.assertEqual(decision.target_range_a1, "'Filtered Revenue'!A1:C25")

    def test_an_uncaptured_overlap_is_never_assumed_empty(self) -> None:
        # The target overlaps the used range and nobody captured it. Silence is
        # not permission.
        unknown = context(sheets=(sheet(used_range_a1="A1:J50"),))

        decision = select_placement(request(), context=unknown)

        self.assertTrue(decision.creates_sheet)
        self.assertEqual(
            {item.kind for item in decision.collisions},
            {CollisionKind.UNCAPTURED},
        )

    def test_a_target_outside_the_used_range_needs_no_capture(self) -> None:
        decision = select_placement(request(), context=context())

        self.assertEqual(decision.collisions, ())
        self.assertIsNone(decision.before_cells)

    def test_structure_blocks_the_target_even_when_its_cells_are_empty(
        self,
    ) -> None:
        merged = context(
            sheets=(sheet(used_range_a1="A1:J50", merged_ranges=("F3:G4",)),),
            candidates=(
                CapturedRange(
                    worksheet_id="sheet-1",
                    range_a1="E1:G25",
                    cells=tuple(
                        tuple(CellState() for _ in range(3)) for _ in range(25)
                    ),
                ),
            ),
        )

        decision = select_placement(request(), context=merged)

        self.assertTrue(decision.creates_sheet)
        self.assertIn(
            CollisionKind.MERGED,
            {item.kind for item in decision.collisions},
        )

    def test_a_fail_collision_policy_refuses_rather_than_relocating(self) -> None:
        blocked = context(
            sheets=(sheet(used_range_a1="A1:J50"),),
            candidates=(occupied_capture("E1:G25"),),
        )

        with self.assertRaises(PlacementError) as caught:
            select_placement(
                request(collision_policy=WorkbookCollisionPolicy.FAIL),
                context=blocked,
            )

        self.assertEqual(caught.exception.code, PlacementFailure.TARGET_OCCUPIED)

    def test_a_result_too_wide_for_the_sheet_moves_to_a_new_sheet(self) -> None:
        decision = select_placement(
            request(source_range_a1="A1:XFD50", output_columns=5),
            context=context(sheets=(sheet(column_count=16_384),)),
        )

        self.assertTrue(decision.creates_sheet)

    def test_a_result_too_large_for_any_sheet_is_refused(self) -> None:
        with self.assertRaises(PlacementError) as caught:
            select_placement(
                request(
                    policy=WorkbookPlacementPolicy.NEW_SHEET,
                    output_columns=20_000,
                ),
                context=context(),
            )

        self.assertEqual(
            caught.exception.code,
            PlacementFailure.OUTPUT_EXCEEDS_SHEET,
        )


class ExactRangePlacementTests(unittest.TestCase):
    def _request(self, **overrides) -> PlacementRequest:
        values = {
            "policy": WorkbookPlacementPolicy.EXACT_RANGE,
            "exact_target_range_a1": "E1:G25",
            "output_rows": 25,
            "output_columns": 3,
        }
        values.update(overrides)
        return request(**values)

    def test_an_empty_exact_range_is_written_without_overwriting(self) -> None:
        decision = select_placement(self._request(), context=context())

        self.assertEqual(decision.target_range_a1, "'Sales'!E1:G25")
        self.assertFalse(decision.overwrites)

    def test_replacement_needs_both_the_request_and_early_approval(self) -> None:
        blocked = context(
            sheets=(sheet(used_range_a1="A1:J50"),),
            candidates=(occupied_capture("E1:G25"),),
        )

        with self.assertRaises(PlacementError) as caught:
            select_placement(
                self._request(replacement_requested=True),
                context=blocked,
            )

        self.assertEqual(
            caught.exception.code,
            PlacementFailure.REPLACEMENT_NOT_APPROVED,
        )

    def test_an_approved_replacement_captures_what_it_overwrites(self) -> None:
        blocked = context(
            sheets=(sheet(used_range_a1="A1:J50"),),
            candidates=(occupied_capture("E1:G25"),),
        )

        decision = select_placement(
            self._request(
                replacement_requested=True,
                early_destructive_approval=True,
            ),
            context=blocked,
        )

        self.assertTrue(decision.overwrites)
        self.assertIsNotNone(decision.before_cells)
        self.assertEqual(len(decision.before_cells or ()), 25)

    def test_an_unrequested_overwrite_relocates_instead(self) -> None:
        blocked = context(
            sheets=(sheet(used_range_a1="A1:J50"),),
            candidates=(occupied_capture("E1:G25"),),
        )

        decision = select_placement(self._request(), context=blocked)

        self.assertTrue(decision.creates_sheet)
        self.assertFalse(decision.overwrites)

    def test_a_result_larger_than_the_requested_range_is_refused(self) -> None:
        with self.assertRaises(PlacementError) as caught:
            select_placement(
                self._request(output_rows=40),
                context=context(),
            )

        self.assertEqual(
            caught.exception.code,
            PlacementFailure.EXACT_TARGET_TOO_SMALL,
        )

    def test_protection_is_never_overwritten_even_when_approved(self) -> None:
        protected = context(
            sheets=(
                sheet(used_range_a1="A1:J50", protected_ranges=("F1:F30",)),
            ),
            candidates=(occupied_capture("E1:G25"),),
        )

        decision = select_placement(
            self._request(
                replacement_requested=True,
                early_destructive_approval=True,
            ),
            context=protected,
        )

        self.assertTrue(decision.creates_sheet)
        self.assertFalse(decision.overwrites)


class SheetNamingTests(unittest.TestCase):
    def test_forbidden_characters_are_removed(self) -> None:
        self.assertEqual(
            sanitize_sheet_name("Q1/Q2 [draft]: *results*?"),
            "Q1 Q2 draft results",
        )

    def test_names_are_trimmed_to_the_spreadsheet_limit(self) -> None:
        self.assertEqual(len(sanitize_sheet_name("x" * 60)), 31)

    def test_an_empty_name_falls_back(self) -> None:
        self.assertEqual(sanitize_sheet_name("///"), "AI Result")

    def test_a_reserved_name_falls_back(self) -> None:
        self.assertEqual(sanitize_sheet_name("History"), "AI Result")

    def test_collisions_resolve_deterministically(self) -> None:
        existing = ("Filtered Revenue", "filtered revenue (2)")

        self.assertEqual(
            unique_sheet_name("Filtered Revenue", existing),
            "Filtered Revenue (3)",
        )

    def test_a_suffix_never_pushes_a_name_past_the_limit(self) -> None:
        name = unique_sheet_name("y" * 31, ("y" * 31,))

        self.assertLessEqual(len(name), 31)
        self.assertTrue(name.endswith(" (2)"))

    def test_generated_worksheet_ids_are_stable_and_distinct(self) -> None:
        first = deterministic_worksheet_id("wb-1", "Results", _RUN_A)

        self.assertEqual(
            first,
            deterministic_worksheet_id("wb-1", "Results", _RUN_A),
        )
        self.assertNotEqual(
            first,
            deterministic_worksheet_id("wb-1", "Results", _RUN_B),
        )


class OccupancyTests(unittest.TestCase):
    def test_a_reserved_rectangle_is_reported_with_its_owner(self) -> None:
        report = inspect_rectangle(
            Rect.from_a1("E1:G25"),
            sheet=sheet(),
            capture=None,
            reserved=((Rect.from_a1("F5:H9"), "run 22222222"),),
        )

        self.assertEqual(
            [item.kind for item in report.collisions],
            [CollisionKind.RESERVED],
        )

    def test_a_rectangle_past_the_sheet_limit_reports_only_that(self) -> None:
        report = inspect_rectangle(
            Rect.from_a1("A1:B2000"),
            sheet=sheet(row_count=1_000),
            capture=None,
        )

        self.assertEqual(
            [item.kind for item in report.collisions],
            [CollisionKind.OUT_OF_BOUNDS],
        )

    def test_formatting_alone_does_not_occupy_a_cell(self) -> None:
        formatted = CapturedRange(
            worksheet_id="sheet-1",
            range_a1="E1:G25",
            cells=tuple(
                tuple(
                    CellState(number_format="#,##0.00") for _ in range(3)
                )
                for _ in range(25)
            ),
        )

        report = inspect_rectangle(
            Rect.from_a1("E1:G25"),
            sheet=sheet(used_range_a1="A1:J50"),
            capture=formatted,
        )

        self.assertTrue(report.is_free)


class WriteReservationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = InMemorySpatialReservationRepository()

    async def test_two_patches_cannot_reserve_overlapping_rectangles(
        self,
    ) -> None:
        await self.repository.reserve(reservation())

        with self.assertRaises(SpatialReservationConflictError):
            await self.repository.reserve(
                reservation(
                    run_id=_RUN_B,
                    patch_id="patch-b",
                    rect=Rect.from_a1("F10:J40"),
                )
            )

    async def test_non_overlapping_patches_on_one_sheet_both_proceed(
        self,
    ) -> None:
        await self.repository.reserve(reservation())

        second = await self.repository.reserve(
            reservation(
                run_id=_RUN_B,
                patch_id="patch-b",
                rect=Rect.from_a1("J1:L25"),
            )
        )

        self.assertEqual(second.status, ReservationStatus.ACTIVE)

    async def test_the_same_patch_revision_reserves_idempotently(self) -> None:
        first = await self.repository.reserve(reservation())
        again = await self.repository.reserve(
            reservation().model_copy(
                update={"reservation_id": first.reservation_id}
            )
        )

        self.assertEqual(again.reservation_id, first.reservation_id)

    async def test_a_newer_revision_supersedes_its_own_predecessor(self) -> None:
        first = await self.repository.reserve(reservation())

        second = await self.repository.reserve(
            reservation(revision=2, rect=Rect.from_a1("E1:G30"))
        )
        active = await self.repository.list_active(
            user_id="user-1",
            workbook_id="wb-1",
            now=_NOW,
        )

        self.assertEqual(second.patch_revision, 2)
        self.assertEqual(
            [item.reservation_id for item in active],
            [second.reservation_id],
        )
        self.assertNotIn(first.reservation_id, {item.reservation_id for item in active})

    async def test_a_released_rectangle_frees_the_area(self) -> None:
        first = await self.repository.reserve(reservation())
        await self.repository.release(
            user_id="user-1",
            reservation_id=first.reservation_id,
            status=ReservationStatus.RELEASED,
            reason="patch_rejected",
        )

        second = await self.repository.reserve(
            reservation(run_id=_RUN_B, patch_id="patch-b")
        )

        self.assertEqual(second.status, ReservationStatus.ACTIVE)

    async def test_an_expired_lease_stops_blocking(self) -> None:
        stale = reservation().model_copy(
            update={"expires_at": _NOW - timedelta(minutes=1)}
        )
        await self.repository.reserve(stale)

        expired = await self.repository.expire_due(now=_NOW)
        second = await self.repository.reserve(
            reservation(run_id=_RUN_B, patch_id="patch-b")
        )

        self.assertEqual(expired, 1)
        self.assertEqual(second.status, ReservationStatus.ACTIVE)

    async def test_the_service_reports_other_runs_rectangles_for_placement(
        self,
    ) -> None:
        service = WriteReservationService(self.repository, clock=lambda: _NOW)
        await self.repository.reserve(reservation())

        occupied = await service.occupied_rectangles(
            user_id="user-1",
            workbook_id="wb-1",
            exclude_run_id=_RUN_B,
        )

        self.assertEqual(
            occupied["sheet-1"],
            ((Rect.from_a1("E1:G25"), f"run {_RUN_A}"),),
        )

    async def test_a_reserved_rectangle_pushes_the_next_run_to_a_new_sheet(
        self,
    ) -> None:
        service = WriteReservationService(self.repository, clock=lambda: _NOW)
        await self.repository.reserve(reservation())

        occupied = await service.occupied_rectangles(
            user_id="user-1",
            workbook_id="wb-1",
            exclude_run_id=_RUN_B,
        )
        decision = select_placement(
            request(),
            context=context(),
            reserved=occupied,
        )

        self.assertTrue(decision.creates_sheet)
        self.assertIn(
            CollisionKind.RESERVED,
            {item.kind for item in decision.collisions},
        )

    async def test_reserving_through_the_service_claims_the_decided_rectangle(
        self,
    ) -> None:
        service = WriteReservationService(self.repository, clock=lambda: _NOW)
        decision = select_placement(request(), context=context())

        claimed = await service.reserve(
            decision,
            request=ReservationRequest(
                user_id="user-1",
                workspace_id="workspace-1",
                workbook_id="wb-1",
                run_id=_RUN_A,
                patch_id="patch-a",
                patch_revision=1,
                base_revision=12,
                lease_owner="test",
            ),
        )

        self.assertEqual(claimed.rect, decision.target_rect)
        self.assertEqual(claimed.worksheet_id, decision.worksheet_id)


class ContextIntegrityTests(unittest.TestCase):
    def test_a_tampered_context_hash_is_refused(self) -> None:
        payload = context().model_dump(mode="python")
        payload["context_hash"] = "b" * 64

        with self.assertRaises(ValueError):
            WorkbookPatchContext.model_validate(payload)

    def test_a_capture_for_an_unknown_sheet_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            context(
                candidates=(
                    CapturedRange(
                        worksheet_id="sheet-missing",
                        range_a1="A1:B2",
                        cells=((CellState(), CellState()),) * 2,
                    ),
                )
            )

    def test_captured_cells_must_match_their_range(self) -> None:
        with self.assertRaises(ValueError):
            CapturedRange(
                worksheet_id="sheet-1",
                range_a1="A1:C3",
                cells=((CellState(), CellState()),),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
