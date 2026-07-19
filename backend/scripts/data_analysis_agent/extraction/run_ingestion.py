from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import qdrant_manager
from db.mongodb import close_mongodb, init_mongodb
from scripts.data_analysis_agent.extraction.pipeline import ingest_data_analysis_pdf
from services.cloudinary_setup import init_cloudinary


SAMPLE_PDF = (
    Path(__file__).resolve().parent.parent
    / "sample_pdfs"
    / "amazon-conservation-team_2023.pdf"
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest a PDF for the DocMind data-analysis agent"
    )
    parser.add_argument("pdf", nargs="?", type=Path, default=SAMPLE_PDF)
    parser.add_argument(
        "--user-id",
        default="data-analysis-local",
        help="Document owner used for MongoDB and Qdrant isolation",
    )
    parser.add_argument(
        "--chat-id",
        default=None,
        help="Optional existing MongoDB chat id to attach the document to",
    )
    parser.add_argument(
        "--wait-for-docling",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Wait for the optional fallback (the API normally runs it in background)",
    )
    return parser.parse_args()


async def _run() -> None:
    args = _arguments()
    await init_mongodb()
    if not init_cloudinary():
        raise RuntimeError("Cloudinary must be configured for end-to-end ingestion")
    try:
        result = await ingest_data_analysis_pdf(
            args.pdf,
            user_id=args.user_id,
            chat_id=args.chat_id,
            wait_for_docling=args.wait_for_docling,
        )
        print(result.model_dump_json(indent=2))
    finally:
        await close_mongodb()
        qdrant_manager.close_sync_client()


if __name__ == "__main__":
    asyncio.run(_run())
