from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Sequence

import fitz

from db.models.structured_table import StructuredTable, TableSourceFragment
from scripts.data_analysis_agent.table_coverage_detector import PageRange
from scripts.data_analysis_agent.table_extractor import (
    TableFragment,
    normalize_table_fragments,
)


class DoclingFallbackError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _bbox_to_top_left(
    bbox: Sequence[float], *, origin: str, page_height: float
) -> list[float]:
    left, top, right, bottom = (float(value) for value in bbox)
    if "BOTTOMLEFT" in origin.upper():
        top, bottom = page_height - top, page_height - bottom
    x0, x1 = sorted((left, right))
    y0, y1 = sorted((top, bottom))
    return [x0, y0, x1, y1]


def _raw_tables_to_fragments(
    pdf_path: Path, raw_tables: Sequence[dict[str, Any]]
) -> list[TableFragment]:
    fragments: list[TableFragment] = []
    with fitz.open(pdf_path) as document:
        for raw_table in raw_tables:
            normalized_sources: list[TableSourceFragment] = []
            first_bbox: list[float] | None = None
            first_page = 0
            for raw_source in raw_table.get("sources", []):
                page_number = int(raw_source.get("page", 0))
                if not 1 <= page_number <= len(document):
                    continue
                page = document[page_number - 1]
                bbox = _bbox_to_top_left(
                    raw_source.get("bounding_box", []),
                    origin=str(raw_source.get("coord_origin", "TOPLEFT")),
                    page_height=float(page.rect.height),
                )
                normalized_sources.append(
                    TableSourceFragment(page=page_number, bounding_box=bbox)
                )
                if first_bbox is None:
                    first_bbox = bbox
                    first_page = page_number
            normalized_sources.sort(key=lambda source: source.page)
            if first_bbox is None or not normalized_sources:
                continue
            first_page = normalized_sources[0].page
            first_bbox = normalized_sources[0].bounding_box
            page = document[first_page - 1]
            header = [str(value or "") for value in raw_table.get("header", [])]
            rows = [
                [str(value or "") for value in row]
                for row in raw_table.get("rows", [])
            ]
            if len(header) < 2 or not rows:
                continue
            fragments.append(
                TableFragment(
                    page=first_page,
                    page_width=float(page.rect.width),
                    page_height=float(page.rect.height),
                    bbox=tuple(first_bbox),  # type: ignore[arg-type]
                    header=header,
                    rows=rows,
                    sources=normalized_sources,
                    title_hint=str(raw_table.get("title", "")),
                    extraction_method="docling",
                )
            )
    return fragments


async def _run_worker(
    pdf_path: Path,
    page_ranges: Sequence[PageRange],
) -> list[dict[str, Any]]:
    if not page_ranges:
        return []
    interpreter = os.getenv("DATA_ANALYSIS_DOCLING_PYTHON", sys.executable)
    ranges_json = json.dumps(
        [page_range.model_dump() for page_range in page_ranges],
        separators=(",", ":"),
    )
    backend_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="docmind-docling-") as temp_dir:
        output_path = Path(temp_dir) / "tables.json"
        process = await asyncio.create_subprocess_exec(
            interpreter,
            "-m",
            "scripts.data_analysis_agent.docling_worker",
            "--pdf",
            str(pdf_path),
            "--ranges",
            ranges_json,
            "--output",
            str(output_path),
            cwd=str(backend_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timeout = float(os.getenv("DATA_ANALYSIS_DOCLING_JOB_TIMEOUT_SECONDS", "900"))
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            raise
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            if not detail:
                detail = stdout.decode("utf-8", errors="replace").strip()
            raise DoclingFallbackError(
                "Docling worker failed. Install backend/requirements-docling.txt "
                "in DATA_ANALYSIS_DOCLING_PYTHON. "
                f"Worker output: {detail[-2000:]}"
            )
        if not output_path.is_file():
            raise DoclingFallbackError("Docling worker returned no table output")
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise DoclingFallbackError("Docling worker output must be a table list")
        return payload


def _page_overlap(left: StructuredTable, right: StructuredTable) -> bool:
    return max(left.page_start, right.page_start) <= min(left.page_end, right.page_end)


def _column_similarity(left: StructuredTable, right: StructuredTable) -> float:
    left_columns = "|".join(_canonical(column.label) for column in left.columns)
    right_columns = "|".join(_canonical(column.label) for column in right.columns)
    return SequenceMatcher(None, left_columns, right_columns).ratio()


def _table_values(table: StructuredTable) -> set[str]:
    return {
        canonical
        for row in table.rows
        for value in row.values()
        if (canonical := _canonical(value))
    }


def _value_similarity(left: StructuredTable, right: StructuredTable) -> float:
    left_values = _table_values(left)
    right_values = _table_values(right)
    if not left_values or not right_values:
        return 0.0
    return len(left_values & right_values) / len(left_values | right_values)


def _bbox_overlap(left: StructuredTable, right: StructuredTable) -> float:
    best = 0.0
    for left_source in left.source_fragments:
        for right_source in right.source_fragments:
            if left_source.page != right_source.page:
                continue
            left_rect = fitz.Rect(left_source.bounding_box)
            right_rect = fitz.Rect(right_source.bounding_box)
            intersection = left_rect & right_rect
            smaller_area = min(left_rect.get_area(), right_rect.get_area())
            if not intersection.is_empty and smaller_area > 0:
                best = max(best, intersection.get_area() / smaller_area)
    return best


def tables_are_duplicates(left: StructuredTable, right: StructuredTable) -> bool:
    if not _page_overlap(left, right):
        return False
    column_similarity = _column_similarity(left, right)
    value_similarity = _value_similarity(left, right)
    title_similarity = SequenceMatcher(
        None, _canonical(left.title), _canonical(right.title)
    ).ratio()
    bbox_overlap = _bbox_overlap(left, right)
    return (
        column_similarity >= 0.82 and value_similarity >= 0.72
    ) or (
        bbox_overlap >= 0.75
        and (
            title_similarity >= 0.85
            or (
                column_similarity >= 0.70
                and (value_similarity >= 0.35 or title_similarity >= 0.65)
            )
        )
    )


def _data_cell_count(table: StructuredTable) -> int:
    return len(table.columns) * len(table.rows)


def merge_unique_tables(
    primary_tables: Sequence[StructuredTable],
    fallback_tables: Sequence[StructuredTable],
) -> tuple[list[StructuredTable], list[StructuredTable], int]:
    """
    Return retained primary tables, new tables needing summaries, and duplicate count.

    A materially more complete Docling duplicate replaces its PyMuPDF version;
    otherwise the already-summarized PyMuPDF table is retained.
    """
    retained = list(primary_tables)
    additions: list[StructuredTable] = []
    duplicate_count = 0
    for candidate in fallback_tables:
        matches = [
            (index, existing)
            for index, existing in enumerate(retained)
            if tables_are_duplicates(existing, candidate)
        ]
        matches.extend(
            (len(retained) + index, existing)
            for index, existing in enumerate(additions)
            if tables_are_duplicates(existing, candidate)
        )
        if not matches:
            additions.append(candidate)
            continue

        duplicate_count += 1
        match_index, existing = max(
            matches, key=lambda item: _data_cell_count(item[1])
        )
        if _data_cell_count(candidate) <= _data_cell_count(existing) * 1.20:
            continue
        if match_index < len(retained):
            retained.pop(match_index)
        else:
            additions.pop(match_index - len(retained))
        additions.append(candidate)

    combined = sorted(
        [*retained, *additions],
        key=lambda table: (table.page_start, table.page_end, table.table_id),
    )
    return combined, additions, duplicate_count


async def extract_tables_with_docling(
    pdf_path: str | Path,
    *,
    page_ranges: Sequence[PageRange],
    document_id: str,
    user_id: str,
    chat_id: str | None = None,
    nodes: Sequence[dict[str, Any]] | None = None,
) -> list[StructuredTable]:
    """Run isolated Docling only for the detector-selected page ranges."""
    path = Path(pdf_path).expanduser().resolve()
    raw_tables = await _run_worker(path, page_ranges)
    fragments = _raw_tables_to_fragments(path, raw_tables)
    return normalize_table_fragments(
        path,
        fragments,
        document_id=document_id,
        user_id=user_id,
        chat_id=chat_id,
        nodes=nodes,
    )
