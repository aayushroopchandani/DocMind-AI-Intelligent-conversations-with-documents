from __future__ import annotations

import unittest

from pydantic import ValidationError

from scripts.data_analysis_agent.runtime.models.requests import (
    ActiveArtifactContext,
    CreateAnalysisRunRequest,
    PdfRunContext,
)
from scripts.data_analysis_agent.runtime.models.runs import AnalysisMode
from scripts.data_analysis_agent.runtime.models.workbook import (
    SpreadsheetContext,
    WorkbookRangeSnapshot,
    a1_dimensions,
    canonical_snapshot_hash,
)


_DOCUMENT_ID = "a" * 64


def _snapshot(range_a1: str) -> WorkbookRangeSnapshot:
    rows, columns = a1_dimensions(range_a1)
    empty_row = (None,) * columns
    empty_matrix = (empty_row,) * rows
    return WorkbookRangeSnapshot(
        range_a1=range_a1,
        values=empty_matrix,
        formulas=empty_matrix,
        cell_types=empty_matrix,
        number_formats=empty_matrix,
        row_count=rows,
        column_count=columns,
    )


def _context(
    *,
    worksheet_name: str = "Sales Data",
    selected_range: str | None = "'Sales Data'!B2:C4",
    used_range: str = "'Sales Data'!A1:F20",
    snapshot_range: str = "'Sales Data'!B2:C4",
    workbook_id: str = "workbook-1",
    worksheet_id: str = "worksheet-1",
) -> SpreadsheetContext:
    snapshot = _snapshot(snapshot_range)
    return SpreadsheetContext(
        workbook_id=workbook_id,
        workbook_name="Revenue model",
        client_revision=12,
        worksheet_id=worksheet_id,
        worksheet_name=worksheet_name,
        selected_range=selected_range,
        used_range=used_range,
        snapshot_range=snapshot_range,
        snapshot_hash=canonical_snapshot_hash(snapshot),
        snapshot=snapshot,
    )


class SpreadsheetContextContractTests(unittest.TestCase):
    def test_snapshot_preserves_exact_cell_strings_and_normalizes_ranges(
        self,
    ) -> None:
        snapshot = WorkbookRangeSnapshot(
            range_a1="  Sheet1!A1:A1  ",
            values=(("  padded identifier  ",),),
            formulas=(('  =A1 & " "  ',),),
            cell_types=(("formula",),),
            number_formats=(("  #,##0.00  ",),),
            column_headers=("  display label  ",),
            row_count=1,
            column_count=1,
            merged_ranges=("  Sheet1!A1:A1  ",),
        )

        self.assertEqual(snapshot.range_a1, "Sheet1!A1:A1")
        self.assertEqual(snapshot.merged_ranges, ("Sheet1!A1:A1",))
        self.assertEqual(snapshot.values, (("  padded identifier  ",),))
        self.assertEqual(snapshot.formulas, (('  =A1 & " "  ',),))
        self.assertEqual(snapshot.number_formats, (("  #,##0.00  ",),))
        self.assertEqual(snapshot.column_headers, ("  display label  ",))

    def test_snapshot_hash_distinguishes_exact_string_content(self) -> None:
        exact = WorkbookRangeSnapshot(
            range_a1="Sheet1!A1:A1",
            values=(("x",),),
            formulas=(("=A1",),),
            cell_types=(("formula",),),
            number_formats=(("#,##0",),),
            row_count=1,
            column_count=1,
        )
        padded_value = WorkbookRangeSnapshot.model_validate(
            {
                **exact.model_dump(mode="python"),
                "values": ((" x ",),),
            }
        )
        padded_formula = WorkbookRangeSnapshot.model_validate(
            {
                **exact.model_dump(mode="python"),
                "formulas": ((" =A1 ",),),
            }
        )
        padded_format = WorkbookRangeSnapshot.model_validate(
            {
                **exact.model_dump(mode="python"),
                "number_formats": ((" #,##0 ",),),
            }
        )

        hashes = {
            canonical_snapshot_hash(exact),
            canonical_snapshot_hash(padded_value),
            canonical_snapshot_hash(padded_formula),
            canonical_snapshot_hash(padded_format),
        }
        self.assertEqual(len(hashes), 4)

    def test_snapshot_uses_selected_range_and_selection_is_contained(self) -> None:
        context = _context(
            selected_range="B2:C4",
            snapshot_range="'Sales Data'!B2:C4",
        )

        self.assertEqual(context.selected_range, "B2:C4")
        self.assertEqual(context.snapshot_range, "'Sales Data'!B2:C4")

    def test_snapshot_uses_used_range_when_there_is_no_selection(self) -> None:
        context = _context(
            selected_range=None,
            used_range="'Sales Data'!A1:B2",
            snapshot_range="A1:B2",
        )

        self.assertIsNone(context.selected_range)

    def test_snapshot_must_match_selected_range(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "snapshot_range must match selected_range",
        ):
            _context(snapshot_range="'Sales Data'!B2:D4")

    def test_snapshot_must_match_used_range_without_selection(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "snapshot_range must match used_range",
        ):
            _context(
                selected_range=None,
                used_range="'Sales Data'!A1:B2",
                snapshot_range="'Sales Data'!A1:A2",
            )

    def test_selected_range_must_be_contained_in_used_range(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "selected_range must be contained in used_range",
        ):
            _context(
                selected_range="'Sales Data'!F20:G21",
                snapshot_range="'Sales Data'!F20:G21",
            )

    def test_all_sheet_qualifiers_must_match_worksheet(self) -> None:
        cases = {
            "used_range": {
                "used_range": "'Other Sheet'!A1:F20",
            },
            "selected_range": {
                "selected_range": "'Other Sheet'!B2:C4",
            },
            "snapshot_range": {
                "snapshot_range": "'Other Sheet'!B2:C4",
            },
        }
        for field_name, overrides in cases.items():
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(
                    ValidationError,
                    rf"{field_name} sheet qualifier must match worksheet_name",
                ):
                    _context(**overrides)

    def test_quoted_sheet_name_with_escaped_apostrophe_is_supported(self) -> None:
        context = _context(
            worksheet_name="Bob's Sales",
            selected_range="'Bob''s Sales'!a1:b2",
            used_range="'Bob''s Sales'!A1:F20",
            snapshot_range="'Bob''s Sales'!A1:B2",
        )

        self.assertEqual(context.selected_range, "'Bob''s Sales'!a1:b2")


class RuntimeRequestIdentifierTests(unittest.TestCase):
    def test_invalid_workspace_id_fails_request_validation(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "workspace_id",
        ):
            CreateAnalysisRunRequest(
                workspace_id="workspace/unsafe",
                mode=AnalysisMode.ANALYSE,
                prompt="Profile the data.",
                selected_document_ids=(_DOCUMENT_ID,),
            )

    def test_invalid_chat_id_fails_request_validation(self) -> None:
        with self.assertRaisesRegex(ValidationError, "chat_id"):
            PdfRunContext(
                document_ids=(_DOCUMENT_ID,),
                chat_id="chat/unsafe",
            )

    def test_invalid_active_artifact_id_fails_request_validation(self) -> None:
        with self.assertRaisesRegex(ValidationError, "artifact_id"):
            ActiveArtifactContext(
                client_artifact_id="client-artifact-1",
                artifact_id="artifact/unsafe",
                artifact_version_id="artifact-version-1",
                artifact_type="pdf",
                name="Annual report",
            )

    def test_invalid_workbook_identifiers_fail_context_validation(self) -> None:
        for field_name, value in (
            ("workbook_id", "workbook/unsafe"),
            ("worksheet_id", "worksheet unsafe"),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValidationError, field_name):
                    _context(**{field_name: value})


if __name__ == "__main__":
    unittest.main()
