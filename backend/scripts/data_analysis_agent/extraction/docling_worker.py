from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Sequence


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Isolated Docling table worker")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--ranges", required=True, help="JSON page ranges")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _clean(value: Any) -> str:
    text = " ".join(str(value or "").replace("\u00a0", " ").split())
    while ". . ." in text:
        text = text.replace(". . .", " ")
    text = " ".join(text.split()).strip(" .")
    return "" if text.casefold() in {"nan", "<na>", "nat", "none"} else text


def _column_name(value: Any, index: int) -> str:
    if isinstance(value, tuple):
        parts = [
            _clean(part)
            for part in value
            if _clean(part) and not _clean(part).casefold().startswith("unnamed:")
        ]
        text = " / ".join(dict.fromkeys(parts))
    else:
        text = _clean(value)
    text = re.sub(r"(?<=,)\.(?=\S)", " ", text)
    text = re.sub(r"(?<=\w)\.(?=\w)", " / ", text)
    if not text or text.casefold().startswith("unnamed:"):
        return f"Column {index + 1}"
    return text


def _bbox_payload(provenance: Any) -> dict[str, Any]:
    bbox = provenance.bbox
    origin = getattr(bbox, "coord_origin", "TOPLEFT")
    origin_value = getattr(origin, "value", str(origin))
    return {
        "page": int(provenance.page_no),
        "bounding_box": [float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b)],
        "coord_origin": str(origin_value),
    }


def _table_payload(table: Any, document: Any) -> dict[str, Any] | None:
    dataframe = table.export_to_dataframe(doc=document)
    if dataframe.empty or len(dataframe.columns) < 2:
        return None
    header = [_column_name(value, index) for index, value in enumerate(dataframe.columns)]
    rows = [
        [_clean(value) for value in row]
        for row in dataframe.itertuples(index=False, name=None)
    ]
    rows = [row for row in rows if sum(bool(value) for value in row) >= 2]
    if not rows:
        return None

    sources = [_bbox_payload(provenance) for provenance in table.prov]
    if not sources:
        return None
    try:
        title = _clean(table.caption_text(document))
    except Exception:
        title = ""
    return {
        "header": header,
        "rows": rows,
        "title": title,
        "sources": sources,
    }


def _converter() -> Any:
    # Imports intentionally live inside the child process. Starting FastAPI or
    # running the lightweight detector never imports Docling/PyTorch.
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    table_mode = os.getenv("DATA_ANALYSIS_DOCLING_TABLE_MODE", "accurate").casefold()
    mode = TableFormerMode.FAST if table_mode == "fast" else TableFormerMode.ACCURATE
    options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=True,
        do_picture_classification=False,
        do_picture_description=False,
        do_chart_extraction=False,
        do_code_enrichment=False,
        do_formula_enrichment=False,
        force_backend_text=True,
        generate_page_images=False,
        generate_picture_images=False,
        generate_table_images=False,
        generate_parsed_pages=False,
        enable_remote_services=False,
        allow_external_plugins=False,
        document_timeout=float(
            os.getenv("DATA_ANALYSIS_DOCLING_RANGE_TIMEOUT_SECONDS", "180")
        ),
        accelerator_options=AcceleratorOptions(
            num_threads=max(1, int(os.getenv("DATA_ANALYSIS_DOCLING_THREADS", "4"))),
            device=os.getenv("DATA_ANALYSIS_DOCLING_DEVICE", "cpu"),
        ),
        table_structure_options=TableStructureOptions(
            do_cell_matching=True,
            mode=mode,
        ),
    )
    return DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
        },
    )


def extract_tables(pdf_path: Path, ranges: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    converter = _converter()
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page_range in ranges:
        start = int(page_range["page_start"])
        end = int(page_range["page_end"])
        result = converter.convert(pdf_path, page_range=(start, end))
        for table in result.document.tables:
            payload = _table_payload(table, result.document)
            if payload is None:
                continue
            fingerprint = hashlib.sha256(
                json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            if fingerprint not in seen:
                seen.add(fingerprint)
                output.append(payload)
    return output


def main() -> None:
    args = _arguments()
    ranges = json.loads(args.ranges)
    lock_path = Path(
        os.getenv("DATA_ANALYSIS_DOCLING_LOCK_FILE", "/tmp/docmind_docling.lock")
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        # One model-heavy conversion at a time across all API worker processes.
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        tables = extract_tables(args.pdf.expanduser().resolve(), ranges)
    args.output.write_text(json.dumps(tables, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
