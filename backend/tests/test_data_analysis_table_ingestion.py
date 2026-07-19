from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from scripts.data_analysis_agent.extraction.utils.table_extractor import (
    extract_tables_from_pdf,
)
from scripts.data_analysis_agent.extraction.utils.table_summarizer import (
    _representative_rows,
    summarize_tables,
)
from scripts.data_analysis_agent.extraction.utils.table_vector_store import (
    table_discovery_payload,
)


SAMPLE_PDF = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "data_analysis_agent"
    / "sample_pdfs"
    / "amazon-conservation-team_2023.pdf"
)


class DataAnalysisTableExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tables = extract_tables_from_pdf(
            SAMPLE_PDF,
            document_id="a" * 64,
            user_id="test-user",
        )

    def test_sample_fragments_are_merged_into_five_logical_tables(self) -> None:
        self.assertEqual(len(self.tables), 5)

        multi_page = next(table for table in self.tables if table.page_start == 27)
        self.assertEqual(multi_page.page_end, 28)
        self.assertEqual(len(multi_page.source_fragments), 2)
        self.assertEqual(len(multi_page.rows), 20)
        self.assertTrue(multi_page.node_id.startswith("node_"))

    def test_wide_year_table_is_joined_on_the_same_page(self) -> None:
        trends = next(
            table for table in self.tables if table.title == "Revenue and Expenses Trends"
        )
        self.assertEqual(len(trends.source_fragments), 2)
        self.assertEqual(
            [column.label for column in trends.columns],
            ["Revenue and Expenses Trends", "2019", "2020", "2021", "2022", "2023"],
        )
        self.assertEqual(trends.rows[0]["column_2023"], 13_178_897)

    def test_visual_alignment_preserves_wrapped_labels_and_numeric_types(self) -> None:
        activity = next(table for table in self.tables if table.page_start == 27)
        row = next(
            row
            for row in activity.rows
            if row["item"] == "Change in Net Assets before Translation Adjustment"
        )
        self.assertEqual(row["column_2023"], 1_053_071)
        self.assertEqual(row["column_2022"], -9_633_193)
        self.assertEqual(activity.columns[1].type, "number")
        self.assertEqual(activity.columns[1].unit, "USD")

    def test_qdrant_payload_contains_summary_metadata_but_not_rows(self) -> None:
        table = self.tables[0].model_copy(deep=True)
        table.short_summary = "Funding support by source and year."
        table.keywords = ["funding", "support"]
        table.summary = f"{table.short_summary}\n{table.deterministic_summary}"
        payload = table_discovery_payload(table)

        self.assertEqual(payload["content_type"], "table_summary")
        self.assertEqual(payload["document_id"], "a" * 64)
        self.assertNotIn("rows", payload)
        self.assertEqual(payload["metrics"], ["column_2023", "column_2022"])


class _ConcurrentFakeSummarizer:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def ainvoke(self, _input):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return {
            "short_summary": "A concise semantic table summary.",
            "keywords": ["financial data", "annual comparison", "USD"],
        }


class ParallelTableSummaryTests(unittest.IsolatedAsyncioTestCase):
    def test_only_first_middle_and_last_rows_are_sent_for_large_tables(self) -> None:
        rows = [{"value": index} for index in range(10)]
        self.assertEqual(
            _representative_rows(rows),
            [{"value": 0}, {"value": 5}, {"value": 9}],
        )

    async def test_table_summaries_are_generated_in_parallel(self) -> None:
        tables = extract_tables_from_pdf(
            SAMPLE_PDF,
            document_id="b" * 64,
            user_id="test-user",
        )
        fake = _ConcurrentFakeSummarizer()
        summarized = await summarize_tables(
            tables,
            summarizer=fake,
            max_concurrency=4,
        )

        self.assertGreaterEqual(fake.max_active, 2)
        self.assertTrue(all(table.short_summary for table in summarized))
        self.assertTrue(all("Keywords:" in table.summary for table in summarized))
        self.assertTrue(all("Contains" in table.summary for table in summarized))


if __name__ == "__main__":
    unittest.main()
