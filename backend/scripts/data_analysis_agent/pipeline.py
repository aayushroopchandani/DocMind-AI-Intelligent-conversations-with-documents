from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from pydantic import BaseModel

from db import crud
from scripts.data_analysis_agent.table_extractor import extract_tables_from_pdf
from scripts.data_analysis_agent.table_summarizer import summarize_tables
from scripts.data_analysis_agent.table_vector_store import (
    delete_table_vectors,
    index_table_summaries,
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


class DataAnalysisIngestionResult(BaseModel):
    document_id: str
    document_db_id: str
    filename: str
    table_count: int
    chunk_size: int = DATA_ANALYSIS_CHUNK_SIZE
    chunk_overlap: int = DATA_ANALYSIS_CHUNK_OVERLAP
    structured_table_collection: str = "structured_tables"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as pdf_file:
        for block in iter(lambda: pdf_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def ingest_data_analysis_pdf(
    pdf_path: str | Path,
    *,
    user_id: str,
    chat_id: str | None = None,
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
        tables = await asyncio.to_thread(
            extract_tables_from_pdf,
            path,
            document_id=document_id,
            user_id=user_id,
            chat_id=chat_id,
            nodes=nodes,
        )
        tables = await summarize_tables(tables)

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

        return DataAnalysisIngestionResult(
            document_id=document_id,
            document_db_id=document_db_id,
            filename=filename,
            table_count=len(tables),
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
