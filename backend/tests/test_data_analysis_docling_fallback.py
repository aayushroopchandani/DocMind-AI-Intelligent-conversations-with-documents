from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from scripts.data_analysis_agent.docling_fallback import merge_unique_tables
from scripts.data_analysis_agent.pipeline import run_docling_table_fallback
from scripts.data_analysis_agent.table_coverage_detector import (
    PageRange,
    TableCoverageReport,
    analyze_pdf_table_coverage,
    group_flagged_pages,
)
from scripts.data_analysis_agent.table_extractor import extract_tables_from_pdf


SAMPLE_DIR = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "data_analysis_agent"
    / "sample_pdfs"
)
ANNUAL_REPORT = SAMPLE_DIR / "PDF Solutions 2024 Annual Report.pdf"
AMAZON_REPORT = SAMPLE_DIR / "amazon-conservation-team_2023.pdf"


class TableCoverageDetectorTests(unittest.TestCase):
    def test_consecutive_flags_are_padded_and_long_runs_are_bounded(self) -> None:
        ranges = group_flagged_pages(
            range(4, 15),
            total_pages=20,
            padding=1,
            max_pages_per_job=7,
        )
        self.assertEqual(
            [(item.page_start, item.page_end) for item in ranges],
            [(3, 9), (8, 14), (13, 15)],
        )
        self.assertTrue(all(item.page_end - item.page_start + 1 <= 7 for item in ranges))

    @unittest.skipUnless(ANNUAL_REPORT.is_file(), "annual-report fixture not present")
    def test_borderless_financial_statements_are_flagged(self) -> None:
        report = analyze_pdf_table_coverage(
            ANNUAL_REPORT,
            page_numbers=range(50, 61),
        )
        self.assertEqual(report.flagged_pages, [54, 55, 56, 57, 58])
        self.assertEqual(
            [item.model_dump() for item in report.page_ranges],
            [
                {
                    "page_start": 53,
                    "page_end": 59,
                    "flagged_pages": [54, 55, 56, 57, 58],
                }
            ],
        )
        page_54 = next(page for page in report.pages if page.page == 54)
        self.assertEqual(page_54.numeric_coverage, 0.0)
        self.assertIn("aligned_numeric_rows_detected", page_54.reasons)
        self.assertIn("no_default_table_detected", page_54.reasons)

    def test_covered_pymupdf_tables_do_not_trigger_fallback(self) -> None:
        report = analyze_pdf_table_coverage(
            AMAZON_REPORT,
            page_numbers=[22, 23, 25, 27, 28],
        )
        self.assertEqual(report.flagged_pages, [])
        self.assertTrue(
            all(page.numeric_coverage == 1.0 for page in report.pages)
        )


class TableDeduplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.primary = extract_tables_from_pdf(
            AMAZON_REPORT,
            document_id="c" * 64,
            user_id="test-user",
        )[0]

    def test_same_docling_table_is_not_added_twice(self) -> None:
        fallback = self.primary.model_copy(deep=True)
        fallback.extraction_method = "docling"
        combined, additions, duplicate_count = merge_unique_tables(
            [self.primary], [fallback]
        )
        self.assertEqual(combined, [self.primary])
        self.assertEqual(additions, [])
        self.assertEqual(duplicate_count, 1)

    def test_more_complete_docling_duplicate_replaces_partial_table(self) -> None:
        partial = self.primary.model_copy(deep=True)
        partial.rows = partial.rows[:3]
        fallback = self.primary.model_copy(deep=True)
        fallback.extraction_method = "docling"
        combined, additions, duplicate_count = merge_unique_tables(
            [partial], [fallback]
        )
        self.assertEqual(combined, [fallback])
        self.assertEqual(additions, [fallback])
        self.assertEqual(duplicate_count, 1)


class QuarantinedPageFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_quarantined_pymupdf_pages_are_added_to_docling_ranges(self) -> None:
        report = TableCoverageReport(
            total_pages=100,
            flagged_pages=[45],
            page_ranges=[
                PageRange(page_start=44, page_end=46, flagged_pages=[45])
            ],
        )
        extract_docling = AsyncMock(return_value=[])
        persist_status = AsyncMock()
        with (
            patch(
                "scripts.data_analysis_agent.pipeline.analyze_pdf_table_coverage",
                return_value=report,
            ),
            patch(
                "scripts.data_analysis_agent.pipeline.extract_tables_with_docling",
                extract_docling,
            ),
            patch(
                "scripts.data_analysis_agent.pipeline.crud.set_table_fallback_status",
                persist_status,
            ),
        ):
            result = await run_docling_table_fallback(
                "unused.pdf",
                document_id="d" * 64,
                user_id="test-user",
                chat_id=None,
                nodes=None,
                primary_tables=[],
                retry_pages=[78],
            )

        page_ranges = extract_docling.await_args.kwargs["page_ranges"]
        self.assertEqual(result.flagged_pages, [45, 78])
        self.assertEqual(
            [
                (item.page_start, item.page_end, item.flagged_pages)
                for item in page_ranges
            ],
            [(44, 46, [45]), (77, 79, [78])],
        )
        self.assertGreaterEqual(persist_status.await_count, 2)


if __name__ == "__main__":
    unittest.main()
