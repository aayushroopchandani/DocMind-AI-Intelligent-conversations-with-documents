from __future__ import annotations

import json
import os
import re
import time
import unittest
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

import fitz

from db.models.structured_table import TableSourceFragment
from scripts.data_analysis_agent.table_coverage_detector import (
    DetectorThresholds,
    _inside_any_table,
    _numeric_alignment,
    _page_words,
    _text_alignment,
    _visual_rows,
    analyze_pdf_table_coverage,
)
from scripts.data_analysis_agent.table_extractor import (
    TableFragment,
    normalize_table_fragments,
)
from scripts.data_analysis_agent.table_validator import validate_tables


RIL_PDF = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "data_analysis_agent"
    / "sample_pdfs"
    / "RIL-Integrated-Annual-Report-2022-23.pdf"
)
_AUDIT_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_AUDIT_NUMBER_RE = re.compile(r"(?<![A-Za-z])\(?[+-]?\d[\d,.]*%?\)?")


def _valid_layout_tables(
    page_data: dict[str, Any],
) -> list[tuple[fitz.Rect, list[list[Any]]]]:
    """Return structurally useful Layout tables, excluding empty detections."""
    tables: list[tuple[fitz.Rect, list[list[Any]]]] = []
    for box in page_data.get("boxes", []):
        if box.get("boxclass") != "table":
            continue
        table = box.get("table") or {}
        row_count = int(table.get("row_count") or 0)
        column_count = int(table.get("col_count") or 0)
        matrix = table.get("extract") or []
        if row_count < 2 or column_count < 2 or not matrix:
            continue

        expected_cells = row_count * column_count
        populated_cells = sum(
            bool(str(value or "").strip())
            for row in matrix
            for value in row[:column_count]
        )
        useful_rows = sum(
            sum(bool(str(value or "").strip()) for value in row[:column_count]) >= 2
            for row in matrix
        )
        if populated_cells / max(1, expected_cells) < 0.25:
            continue
        if useful_rows / max(1, row_count) < 0.40:
            continue

        bbox = table.get("bbox") or [
            box.get("x0"),
            box.get("y0"),
            box.get("x1"),
            box.get("y1"),
        ]
        if len(bbox) != 4 or any(value is None for value in bbox):
            continue
        rect = fitz.Rect(bbox)
        if not rect.is_empty and rect.get_area() > 0:
            tables.append((rect, matrix))
    return tables


def _valid_layout_table_boxes(page_data: dict[str, Any]) -> list[fitz.Rect]:
    return [rect for rect, _ in _valid_layout_tables(page_data)]


def _word_coverage(words: Sequence[Any], boxes: Sequence[fitz.Rect]) -> float:
    if not words or not boxes:
        return 0.0
    covered = sum(_inside_any_table(word, boxes) for word in words)
    return covered / len(words)


def _canonical_tokens(value: Any) -> Counter[str]:
    return Counter(
        token.casefold()
        for token in _AUDIT_TOKEN_RE.findall(str(value or ""))
        if len(token) >= 2
    )


def _canonical_numbers(value: Any) -> Counter[str]:
    values: Counter[str] = Counter()
    for match in _AUDIT_NUMBER_RE.findall(str(value or "")):
        canonical = match.strip("()%+").replace(",", "").lstrip("0") or "0"
        values[canonical] += 1
    return values


def _counter_recall(expected: Counter[str], observed: Counter[str]) -> float:
    if not expected:
        return 1.0
    matched = sum(min(count, observed[token]) for token, count in expected.items())
    return matched / expected.total()


def _layout_fragments(
    page_data_by_number: dict[int, dict[str, Any]],
    pages: Sequence[int],
    document: fitz.Document,
) -> list[TableFragment]:
    fragments: list[TableFragment] = []
    for page_number in pages:
        page = document[page_number - 1]
        for rect, matrix in _valid_layout_tables(
            page_data_by_number.get(page_number, {})
        ):
            width = max((len(row) for row in matrix), default=0)
            rectangular = [
                [str(row[index] or "").strip() if index < len(row) else "" for index in range(width)]
                for row in matrix
            ]
            if len(rectangular) < 2 or width < 2:
                continue
            bbox = tuple(float(value) for value in rect)
            fragments.append(
                TableFragment(
                    page=page_number,
                    page_width=float(page.rect.width),
                    page_height=float(page.rect.height),
                    bbox=bbox,
                    header=rectangular[0],
                    rows=rectangular[1:],
                    sources=[
                        TableSourceFragment(
                            page=page_number,
                            bounding_box=list(bbox),
                        )
                    ],
                    # Reuse the strict PyMuPDF validation branch. The storage
                    # schema deliberately has no production Layout source yet.
                    extraction_method="pymupdf",
                )
            )
    return fragments


def _fidelity_for_fragment(
    fragment: TableFragment, page: fitz.Page
) -> dict[str, Any]:
    matrix_values = [*fragment.header, *(value for row in fragment.rows for value in row)]
    extracted_text = " ".join(matrix_values)
    source_text = page.get_text("text", clip=fitz.Rect(fragment.bbox), sort=True)
    expected_tokens = _canonical_tokens(extracted_text)
    expected_numbers = _canonical_numbers(extracted_text)
    populated = sum(bool(value.strip()) for value in matrix_values)
    expected_cells = len(fragment.header) * (len(fragment.rows) + 1)
    return {
        "page": fragment.page,
        "rows": len(fragment.rows) + 1,
        "columns": len(fragment.header),
        "density": round(populated / max(1, expected_cells), 4),
        "token_fidelity": round(
            _counter_recall(expected_tokens, _canonical_tokens(source_text)), 4
        ),
        "numeric_fidelity": round(
            _counter_recall(expected_numbers, _canonical_numbers(source_text)), 4
        ),
    }


def _assessment_resolved_by_boxes(
    assessment: Any,
    page: fitz.Page,
    boxes: Sequence[fitz.Rect],
    thresholds: DetectorThresholds,
) -> bool:
    if not boxes:
        return False
    words = _page_words(page)
    visual_rows = _visual_rows(words, thresholds.row_tolerance)
    numeric = _numeric_alignment(visual_rows, thresholds)
    textual = _text_alignment(visual_rows, thresholds)
    numeric_coverage = _word_coverage(numeric.words, boxes)
    text_coverage = _word_coverage(textual.words, boxes)
    numeric_resolved = (
        assessment.aligned_numeric_row_count >= thresholds.numeric_min_rows
        and assessment.repeated_numeric_columns >= thresholds.numeric_min_columns
        and assessment.aligned_numeric_value_count >= thresholds.numeric_min_values
        and numeric_coverage >= thresholds.numeric_coverage
    )
    text_resolved = (
        assessment.aligned_text_row_count >= thresholds.text_min_rows
        and assessment.repeated_text_columns >= thresholds.text_min_columns
        and text_coverage >= thresholds.numeric_coverage
    )
    return numeric_resolved or text_resolved


@unittest.skipUnless(
    os.getenv("RUN_PYMUPDF_LAYOUT_BENCHMARK") == "1",
    "set RUN_PYMUPDF_LAYOUT_BENCHMARK=1 to run the 267-page benchmark",
)
class PyMuPDFLayoutCoverageBenchmark(unittest.TestCase):
    def test_ril_docling_page_percentage_after_layout(self) -> None:
        self.assertTrue(RIL_PDF.is_file(), f"Missing benchmark PDF: {RIL_PDF}")
        thresholds = DetectorThresholds.from_env()

        baseline_started = time.perf_counter()
        baseline = analyze_pdf_table_coverage(RIL_PDF, thresholds=thresholds)
        baseline_seconds = time.perf_counter() - baseline_started

        # Importing current PyMuPDF4LLM activates its bundled Layout engine.
        # It deliberately happens after the standard PyMuPDF baseline above.
        import pymupdf4llm

        layout_started = time.perf_counter()
        raw_layout = pymupdf4llm.to_json(
            str(RIL_PDF),
            pages=range(baseline.total_pages),
            use_ocr=False,
            force_ocr=False,
            write_images=False,
            embed_images=False,
            force_text=True,
            show_progress=True,
        )
        layout_seconds = time.perf_counter() - layout_started
        layout_document = (
            json.loads(raw_layout) if isinstance(raw_layout, str) else raw_layout
        )
        page_data_by_number = {
            int(page_data["page_number"]): page_data
            for page_data in layout_document.get("pages", [])
        }

        unresolved_pages: list[int] = []
        resolved_pages: list[int] = []
        pages_with_valid_layout_tables: set[int] = set()

        with fitz.open(RIL_PDF) as document:
            for assessment in baseline.pages:
                page_data = page_data_by_number.get(assessment.page, {})
                layout_boxes = _valid_layout_table_boxes(page_data)
                if layout_boxes:
                    pages_with_valid_layout_tables.add(assessment.page)
                if not assessment.needs_docling:
                    continue
                if not layout_boxes:
                    unresolved_pages.append(assessment.page)
                    continue
                if _assessment_resolved_by_boxes(
                    assessment,
                    document[assessment.page - 1],
                    layout_boxes,
                    thresholds,
                ):
                    resolved_pages.append(assessment.page)
                else:
                    unresolved_pages.append(assessment.page)

            fragments = _layout_fragments(
                page_data_by_number, resolved_pages, document
            )
            fidelity = [
                _fidelity_for_fragment(fragment, document[fragment.page - 1])
                for fragment in fragments
            ]

        normalized_tables = normalize_table_fragments(
            RIL_PDF,
            fragments,
            document_id="ril-pymupdf-layout-quality-benchmark",
            user_id="benchmark",
        )
        validation = validate_tables(normalized_tables)
        accepted_boxes_by_page: dict[int, list[fitz.Rect]] = {}
        for table in validation.accepted:
            for source in table.source_fragments:
                accepted_boxes_by_page.setdefault(source.page, []).append(
                    fitz.Rect(source.bounding_box)
                )

        quality_resolved_pages: list[int] = []
        quality_rejected_pages: list[int] = []
        assessment_by_page = {item.page: item for item in baseline.pages}
        with fitz.open(RIL_PDF) as document:
            for page_number in resolved_pages:
                if _assessment_resolved_by_boxes(
                    assessment_by_page[page_number],
                    document[page_number - 1],
                    accepted_boxes_by_page.get(page_number, []),
                    thresholds,
                ):
                    quality_resolved_pages.append(page_number)
                else:
                    quality_rejected_pages.append(page_number)

        weakest_fidelity = sorted(
            fidelity,
            key=lambda item: (
                item["numeric_fidelity"],
                item["token_fidelity"],
                item["density"],
            ),
        )[:12]

        total_pages = baseline.total_pages
        baseline_flagged = len(baseline.flagged_pages)
        final_flagged = len(unresolved_pages)
        result = {
            "pdf": RIL_PDF.name,
            "total_pages": total_pages,
            "ocr_enabled": bool(layout_document.get("use_ocr")),
            "layout_full_ocr_pages": sum(
                bool(page.get("full_ocred"))
                for page in layout_document.get("pages", [])
            ),
            "baseline_flagged_pages": baseline_flagged,
            "baseline_flagged_percent": round(
                100 * baseline_flagged / total_pages, 2
            ),
            "layout_table_pages": len(pages_with_valid_layout_tables),
            "baseline_pages_resolved_by_layout": len(resolved_pages),
            "final_docling_pages": final_flagged,
            "final_docling_percent": round(100 * final_flagged / total_pages, 2),
            "layout_tables_on_resolved_pages": len(fragments),
            "layout_normalized_tables": len(normalized_tables),
            "layout_strictly_accepted_tables": len(validation.accepted),
            "layout_quarantined_tables": len(validation.quarantined),
            "layout_rejected_tables": len(validation.rejected),
            "quality_resolved_pages": len(quality_resolved_pages),
            "quality_rejected_pages": quality_rejected_pages,
            "quality_adjusted_docling_pages": len(unresolved_pages)
            + len(quality_rejected_pages),
            "quality_adjusted_docling_percent": round(
                100
                * (len(unresolved_pages) + len(quality_rejected_pages))
                / total_pages,
                2,
            ),
            "mean_token_fidelity": round(
                sum(item["token_fidelity"] for item in fidelity)
                / max(1, len(fidelity)),
                4,
            ),
            "mean_numeric_fidelity": round(
                sum(item["numeric_fidelity"] for item in fidelity)
                / max(1, len(fidelity)),
                4,
            ),
            "weakest_fidelity_tables": weakest_fidelity,
            "baseline_seconds": round(baseline_seconds, 3),
            "layout_seconds": round(layout_seconds, 3),
            "resolved_pages": resolved_pages,
            "unresolved_pages": unresolved_pages,
        }
        print("PYMUPDF_LAYOUT_COVERAGE_RESULT=" + json.dumps(result, sort_keys=True))

        self.assertEqual(total_pages, 267)
        self.assertEqual(len(page_data_by_number), total_pages)
        self.assertFalse(result["ocr_enabled"])
        self.assertEqual(result["layout_full_ocr_pages"], 0)
        self.assertLessEqual(final_flagged, baseline_flagged)
        self.assertLessEqual(len(quality_resolved_pages), len(resolved_pages))


if __name__ == "__main__":
    unittest.main()
