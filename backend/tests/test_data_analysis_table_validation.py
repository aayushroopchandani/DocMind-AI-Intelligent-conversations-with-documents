from __future__ import annotations

import unittest
from typing import Any, Sequence

from db.models.structured_table import (
    StructuredTable,
    TableColumn,
    TableSourceFragment,
)
from scripts.data_analysis_agent.extraction.utils.table_validator import (
    assess_table_quality,
    validate_tables,
)


def _table(
    *,
    title: str,
    labels: Sequence[str],
    rows: Sequence[Sequence[Any]],
    extraction_method: str = "pymupdf",
    page: int = 1,
) -> StructuredTable:
    columns = [
        TableColumn(
            key=f"column_{index}",
            label=label,
            type=(
                "number"
                if any(
                    isinstance(row[index - 1], (int, float))
                    for row in rows
                    if len(row) >= index
                )
                else "string"
            ),
        )
        for index, label in enumerate(labels, start=1)
    ]
    normalized_rows = [
        {
            column.key: row[index] if index < len(row) else None
            for index, column in enumerate(columns)
        }
        for row in rows
    ]
    return StructuredTable(
        table_id=f"table-{page}-{abs(hash((title, extraction_method))) % 10_000}",
        document_id="d" * 64,
        user_id="test-user",
        page_start=page,
        page_end=page,
        title=title,
        extraction_method=extraction_method,
        columns=columns,
        rows=normalized_rows,
        source_fragments=[
            TableSourceFragment(page=page, bounding_box=[10, 20, 500, 600])
        ],
    )


class SourceAwareTableValidationTests(unittest.TestCase):
    def test_malformed_pymupdf_fragment_is_quarantined_before_llm(self) -> None:
        malformed = _table(
            page=78,
            title=(
                "Year Ended December 31, 2024 2023 2022 (In thousands) "
                "U.S. Current . . . . . . . . . . . . . . . . . . . . "
                ". . . . . . . . . . . . . . . . . $ 499 $ (353)"
            ),
            labels=[
                "Column 1",
                "The reconciliation between the statutory federal income tax "
                "expense and the Company’s effective income tax expense",
                "Column 3",
            ],
            rows=[["were as follows (in thousands):", None, "y y"]],
        )

        result = validate_tables([malformed])
        assessment = result.assessments[0]

        self.assertEqual(result.accepted, [])
        self.assertEqual(result.quarantined, [malformed])
        self.assertEqual(result.quarantined_pages, [78])
        self.assertLess(assessment.score, 0.70)
        self.assertIn("generic_headers", assessment.reasons)
        self.assertIn("sentence_used_as_header", assessment.reasons)
        self.assertIn("title_looks_like_table_content", assessment.reasons)

    def test_same_broken_fragment_from_docling_is_rejected(self) -> None:
        malformed = _table(
            title="A broken extracted row . . . . . . . . $ 10 $ 20 $ 30 $ 40",
            labels=["Column 1", "Column 2", "Description"],
            rows=[["were as follows:", None, "x x"]],
            extraction_method="docling",
        )

        assessment = assess_table_quality(malformed)

        self.assertEqual(assessment.status, "rejected")

    def test_legitimate_single_row_financial_table_is_accepted(self) -> None:
        financial = _table(
            title="Stock options exercised",
            labels=["Metric", "2024", "2023", "2022"],
            rows=[["Stock options exercised", 30, 42, 51]],
        )

        assessment = assess_table_quality(financial)

        self.assertEqual(assessment.status, "accepted")
        self.assertGreaterEqual(assessment.score, 0.70)

    def test_legitimate_text_comparison_is_accepted(self) -> None:
        comparison = _table(
            title="Model feature comparison",
            labels=["Feature", "GPT-5", "Claude"],
            rows=[["Context", "Large", "Large"]],
        )

        assessment = assess_table_quality(comparison)

        self.assertEqual(assessment.status, "accepted")

    def test_navigation_tables_are_rejected_for_both_extractors(self) -> None:
        for extraction_method in ("pymupdf", "docling"):
            with self.subTest(extraction_method=extraction_method):
                contents = _table(
                    title="TABLE OF CONTENTS",
                    labels=["Section", "Page"],
                    rows=[["Financial Statements", 49], ["Notes", 60]],
                    extraction_method=extraction_method,
                )
                exhibit_index = _table(
                    title="Exhibit Number",
                    labels=["Exhibit Number", "Exhibit Description", "Form"],
                    rows=[["10.1", "Material agreement", "10-K"]],
                    extraction_method=extraction_method,
                )

                result = validate_tables([contents, exhibit_index])

                self.assertEqual(result.accepted, [])
                self.assertEqual(result.quarantined, [])
                self.assertEqual(result.rejected, [contents, exhibit_index])

    def test_useful_exhibit_comparison_is_not_rejected_by_keyword(self) -> None:
        comparison = _table(
            title="Exhibit feature comparison",
            labels=["Feature", "Plan A", "Plan B"],
            rows=[["Support", "Included", "Optional"]],
            extraction_method="docling",
        )

        assessment = assess_table_quality(comparison)

        self.assertEqual(assessment.status, "accepted")

    def test_docling_keeps_dense_multirow_table_with_imperfect_headers(self) -> None:
        useful = _table(
            title=(
                "Property and equipment are depreciated using the straight-line "
                "method over the estimated useful lives of the related assets"
            ),
            labels=["Column 1", "1"],
            rows=[
                ["Computer equipment", 3],
                ["Software development cost", 3],
                ["Furniture and fixtures", "5-10"],
                ["Laboratory equipment", "3-10"],
            ],
            extraction_method="docling",
        )

        assessment = assess_table_quality(useful)

        self.assertEqual(assessment.status, "accepted")

    def test_accepted_unit_only_title_is_repaired_without_an_llm(self) -> None:
        table = _table(
            title="(Dollars in thousands)",
            labels=[
                "(Dollars in thousands)",
                "Year Ended December 31, 2024",
                "Year Ended December 31, 2023",
            ],
            rows=[["Income tax expense", -2522, -1764]],
            extraction_method="docling",
        )
        table.deterministic_summary = (
            "Table: (Dollars in thousands)\n"
            "Columns: (Dollars in thousands), 2024, 2023\n"
            "Contains 1 rows."
        )

        result = validate_tables([table])

        self.assertEqual(result.accepted, [table])
        self.assertEqual(table.title, "Income tax expense by 2024, 2023")
        self.assertTrue(
            table.deterministic_summary.startswith(
                "Table: Income tax expense by 2024, 2023\n"
            )
        )


if __name__ == "__main__":
    unittest.main()
