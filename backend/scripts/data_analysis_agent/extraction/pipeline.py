from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel, Field

from db import crud
from db.models.structured_table import StructuredTable
from scripts.data_analysis_agent.extraction.docling_fallback import (
    extract_tables_with_docling,
    merge_unique_tables,
)
from scripts.data_analysis_agent.extraction.utils.table_coverage_detector import (
    DetectorThresholds,
    TableCoverageReport,
    analyze_pdf_table_coverage,
    group_flagged_pages,
)
from scripts.data_analysis_agent.extraction.utils.table_extractor import (
    extract_tables_from_pdf,
)
from scripts.data_analysis_agent.extraction.utils.table_summarizer import (
    summarize_tables,
)
from scripts.data_analysis_agent.extraction.utils.table_validator import (
    TableValidationResult,
    validate_tables,
)
from scripts.data_analysis_agent.extraction.utils.table_vector_store import (
    delete_table_vectors,
    delete_table_vectors_by_ids,
    index_table_summaries,
    upsert_table_summaries,
)
from scripts.ingest import delete_pdf_embeddings, ingest_pdf_path
from scripts.intention_pipelines.summarization_pipeline.summary_index import (
    SUMMARY_INDEX_VERSION,
    initialize_pending_summary_indexes,
)
from services.cloudinary_setup import delete_pdf, upload_pdf
from utils.pydantic_schemas import IngestData


DATA_ANALYSIS_CHUNK_SIZE = 2400
DATA_ANALYSIS_CHUNK_OVERLAP = 300

logger = logging.getLogger(__name__)
_background_docling_tasks: set[asyncio.Task["TableFallbackResult"]] = set()


class TableFallbackResult(BaseModel):
    status: str
    flagged_pages: list[int] = Field(default_factory=list)
    page_ranges: list[dict[str, Any]] = Field(default_factory=list)
    recovered_table_count: int = 0
    duplicate_table_count: int = 0
    final_table_count: int = 0
    error: str | None = None


class DataAnalysisIngestionResult(BaseModel):
    document_id: str
    document_db_id: str
    filename: str
    table_count: int
    chunk_size: int = DATA_ANALYSIS_CHUNK_SIZE
    chunk_overlap: int = DATA_ANALYSIS_CHUNK_OVERLAP
    structured_table_collection: str = "structured_tables"
    table_fallback_status: str = "scheduled"
    table_fallback_recovered_count: int = 0


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as pdf_file:
        for block in iter(lambda: pdf_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _docling_fallback_enabled() -> bool:
    return os.getenv("DATA_ANALYSIS_DOCLING_ENABLED", "true").casefold() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _log_table_validation(stage: str, result: TableValidationResult) -> None:
    logger.info(
        "%s table validation: %d accepted, %d quarantined, %d rejected",
        stage,
        len(result.accepted),
        len(result.quarantined),
        len(result.rejected),
    )
    for assessment in result.assessments:
        if assessment.status != "accepted":
            logger.debug(
                "%s table %s %s at score %.4f: %s",
                stage,
                assessment.table_id,
                assessment.status,
                assessment.score,
                ", ".join(assessment.reasons),
            )


async def run_docling_table_fallback(
    pdf_path: str | Path,
    *,
    document_id: str,
    user_id: str,
    chat_id: str | None,
    nodes: Sequence[dict[str, Any]] | None,
    primary_tables: Sequence[StructuredTable],
    retry_pages: Sequence[int] = (),
) -> TableFallbackResult:
    """Detect missed tables and enrich doubtful pages without risking base ingestion."""
    path = Path(pdf_path).expanduser().resolve()
    report: TableCoverageReport | None = None
    try:
        await crud.set_table_fallback_status(
            user_id=user_id,
            document_id=document_id,
            status="detecting",
        )
        detector_thresholds = DetectorThresholds.from_env()
        report = await asyncio.to_thread(
            analyze_pdf_table_coverage,
            path,
            thresholds=detector_thresholds,
        )
        flagged_pages = sorted({*report.flagged_pages, *retry_pages})
        if flagged_pages != report.flagged_pages:
            report = report.model_copy(
                update={
                    "flagged_pages": flagged_pages,
                    "page_ranges": group_flagged_pages(
                        flagged_pages,
                        total_pages=report.total_pages,
                        padding=detector_thresholds.page_padding,
                        max_pages_per_job=detector_thresholds.max_pages_per_job,
                    ),
                }
            )
        page_ranges = [page_range.model_dump() for page_range in report.page_ranges]
        if not report.flagged_pages:
            await crud.set_table_fallback_status(
                user_id=user_id,
                document_id=document_id,
                status="not_needed",
                flagged_pages=[],
                page_ranges=[],
                recovered_count=0,
                table_count=len(primary_tables),
            )
            return TableFallbackResult(
                status="not_needed",
                final_table_count=len(primary_tables),
            )

        await crud.set_table_fallback_status(
            user_id=user_id,
            document_id=document_id,
            status="processing",
            flagged_pages=report.flagged_pages,
            page_ranges=page_ranges,
        )
        docling_tables = await extract_tables_with_docling(
            path,
            page_ranges=report.page_ranges,
            document_id=document_id,
            user_id=user_id,
            chat_id=chat_id,
            nodes=nodes,
        )
        docling_validation = validate_tables(docling_tables)
        _log_table_validation("Docling", docling_validation)
        combined, additions, duplicate_count = merge_unique_tables(
            primary_tables, docling_validation.accepted
        )
        if additions:
            await summarize_tables(additions)
            removed_ids = sorted(
                {table.table_id for table in primary_tables}
                - {table.table_id for table in combined}
            )
            await asyncio.to_thread(upsert_table_summaries, additions)
            if removed_ids:
                await asyncio.to_thread(
                    delete_table_vectors_by_ids,
                    user_id=user_id,
                    table_ids=removed_ids,
                )
            await crud.replace_document_tables(
                user_id=user_id,
                document_id=document_id,
                tables=[table.model_dump() for table in combined],
            )

        await crud.set_table_fallback_status(
            user_id=user_id,
            document_id=document_id,
            status="ready",
            flagged_pages=report.flagged_pages,
            page_ranges=page_ranges,
            recovered_count=len(additions),
            table_count=len(combined),
        )
        return TableFallbackResult(
            status="ready",
            flagged_pages=report.flagged_pages,
            page_ranges=page_ranges,
            recovered_table_count=len(additions),
            duplicate_table_count=duplicate_count,
            final_table_count=len(combined),
        )
    except Exception as exc:
        logger.exception("Docling table fallback failed for %s", document_id)
        try:
            await crud.set_table_fallback_status(
                user_id=user_id,
                document_id=document_id,
                status="failed",
                flagged_pages=report.flagged_pages if report else None,
                page_ranges=(
                    [page_range.model_dump() for page_range in report.page_ranges]
                    if report
                    else None
                ),
                recovered_count=0,
                table_count=len(primary_tables),
                error=str(exc),
            )
        except Exception:
            logger.exception("Could not persist Docling failure for %s", document_id)
        return TableFallbackResult(
            status="failed",
            flagged_pages=report.flagged_pages if report else [],
            page_ranges=(
                [page_range.model_dump() for page_range in report.page_ranges]
                if report
                else []
            ),
            final_table_count=len(primary_tables),
            error=str(exc),
        )


def schedule_docling_table_fallback(**kwargs: Any) -> asyncio.Task[TableFallbackResult]:
    task = asyncio.create_task(run_docling_table_fallback(**kwargs))
    _background_docling_tasks.add(task)

    def _finish(done_task: asyncio.Task[TableFallbackResult]) -> None:
        _background_docling_tasks.discard(done_task)
        if done_task.cancelled():
            return
        result = done_task.result()
        logger.info(
            "Docling fallback %s: %s recovered table(s)",
            result.status,
            result.recovered_table_count,
        )

    task.add_done_callback(_finish)
    return task


async def cancel_docling_table_fallbacks() -> None:
    tasks = list(_background_docling_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def ingest_data_analysis_pdf(
    pdf_path: str | Path,
    *,
    user_id: str,
    chat_id: str | None = None,
    wait_for_docling: bool = False,
) -> DataAnalysisIngestionResult:
    """Run Cloudinary, text RAG, structured-table, MongoDB, and Qdrant ingestion."""
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file() or path.suffix.casefold() != ".pdf":
        raise ValueError(f"A readable PDF path is required: {path}")
    if not user_id.strip():
        raise ValueError("user_id is required for document ownership")

    filename = path.name
    document_id = await asyncio.to_thread(sha256_file, path)
    ready_document = await crud.get_ready_document(
        user_id=user_id, document_id=document_id
    )
    claimed = False
    if ready_document is None:
        pending_document, claimed = await crud.create_pending_document(
            user_id=user_id,
            document_id=document_id,
            filename=filename,
        )
        if not claimed:
            if pending_document.get("ingestion_status") != "ready":
                raise RuntimeError(
                    f"'{filename}' is already being processed for this user"
                )
            ready_document = pending_document
    else:
        pending_document = ready_document

    await crud.set_table_ingestion_status(
        user_id=user_id,
        document_id=document_id,
        status="processing",
    )

    asset: dict | None = None
    try:
        if claimed:
            file_bytes = await asyncio.to_thread(path.read_bytes)
            asset = await asyncio.to_thread(
                upload_pdf,
                file_bytes,
                user_id=user_id,
                chat_id=chat_id or "data-analysis",
                filename=filename,
            )
            secure_url = asset["secure_url"]
        else:
            secure_url = str((ready_document or {}).get("secure_url", ""))

        ingest_data = IngestData(
            secure_url=secure_url or "local://data-analysis",
            filename=filename,
            document_id=document_id,
            user_id=user_id,
        )
        nodes = await asyncio.to_thread(
            ingest_pdf_path,
            str(path),
            ingest_data,
            chunk_size=DATA_ANALYSIS_CHUNK_SIZE,
            chunk_overlap=DATA_ANALYSIS_CHUNK_OVERLAP,
            replace_existing=not claimed,
        )
        extracted_tables = await asyncio.to_thread(
            extract_tables_from_pdf,
            path,
            document_id=document_id,
            user_id=user_id,
            chat_id=chat_id,
            nodes=nodes,
        )
        primary_validation = validate_tables(extracted_tables)
        _log_table_validation("PyMuPDF", primary_validation)
        tables = await summarize_tables(primary_validation.accepted)

        if tables:
            await asyncio.to_thread(index_table_summaries, tables)
        else:
            await asyncio.to_thread(
                delete_table_vectors,
                user_id=user_id,
                document_id=document_id,
            )
        await crud.replace_document_tables(
            user_id=user_id,
            document_id=document_id,
            tables=[table.model_dump() for table in tables],
        )

        if claimed:
            assert asset is not None
            indexed_nodes = initialize_pending_summary_indexes(nodes)
            ready_document = await crud.mark_document_ready(
                document_db_id=pending_document["id"],
                user_id=user_id,
                metadata={
                    "public_id": asset["public_id"],
                    "private_id": asset["asset_id"],
                    "secure_url": asset["secure_url"],
                    "resource_type": asset["resource_type"],
                    "filename": asset["filename"],
                    "bytes": asset.get("bytes"),
                    "pages": asset.get("pages"),
                    "nodes": {
                        "nodes": indexed_nodes,
                        "ingestion_status": "not_ready",
                    },
                    "summary_index_status": "pending",
                    "summary_index_version": SUMMARY_INDEX_VERSION,
                    "table_ingestion_status": "ready",
                    "table_count": len(tables),
                    "table_fallback_status": (
                        "pending" if _docling_fallback_enabled() else "disabled"
                    ),
                },
            )
        else:
            await crud.set_table_ingestion_status(
                user_id=user_id,
                document_id=document_id,
                status="ready",
                table_count=len(tables),
            )

        if chat_id:
            await crud.attach_document_to_chat(
                chat_id=chat_id,
                user_id=user_id,
                document_db_id=ready_document["id"],
            )
        document_db_id = (ready_document or pending_document)["id"]

        fallback_result: TableFallbackResult | None = None
        if _docling_fallback_enabled():
            fallback_task = schedule_docling_table_fallback(
                pdf_path=path,
                document_id=document_id,
                user_id=user_id,
                chat_id=chat_id,
                nodes=nodes,
                primary_tables=tables,
                retry_pages=primary_validation.quarantined_pages,
            )
            if wait_for_docling:
                fallback_result = await fallback_task
        else:
            await crud.set_table_fallback_status(
                user_id=user_id,
                document_id=document_id,
                status="disabled",
                recovered_count=0,
                table_count=len(tables),
            )

        return DataAnalysisIngestionResult(
            document_id=document_id,
            document_db_id=document_db_id,
            filename=filename,
            table_count=(
                fallback_result.final_table_count if fallback_result else len(tables)
            ),
            table_fallback_status=(
                fallback_result.status
                if fallback_result
                else "scheduled" if _docling_fallback_enabled() else "disabled"
            ),
            table_fallback_recovered_count=(
                fallback_result.recovered_table_count if fallback_result else 0
            ),
        )
    except Exception as exc:
        if claimed:
            cleanup_steps = (
                (
                    "MongoDB table cleanup",
                    lambda: crud.delete_document_tables(
                        user_id=user_id,
                        document_id=document_id,
                    ),
                ),
                (
                    "table-vector cleanup",
                    lambda: asyncio.to_thread(
                        delete_table_vectors,
                        user_id=user_id,
                        document_id=document_id,
                    ),
                ),
                (
                    "chunk-vector cleanup",
                    lambda: asyncio.to_thread(
                        delete_pdf_embeddings,
                        user_id=user_id,
                        document_id=document_id,
                    ),
                ),
            )
            for label, cleanup_factory in cleanup_steps:
                try:
                    await cleanup_factory()
                except Exception:
                    logger.exception("%s failed for %s", label, document_id)
            if asset is not None:
                try:
                    await asyncio.to_thread(
                        delete_pdf,
                        asset["public_id"],
                        resource_type=asset.get("resource_type", "raw"),
                    )
                except Exception:
                    logger.exception("Cloudinary cleanup failed for %s", document_id)
            try:
                await crud.discard_pending_document(
                    document_db_id=pending_document["id"], user_id=user_id
                )
            except Exception:
                logger.exception("Pending-document cleanup failed for %s", document_id)
        else:
            await crud.set_table_ingestion_status(
                user_id=user_id,
                document_id=document_id,
                status="failed",
                error=str(exc),
            )
        raise
