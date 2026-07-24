from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from db.mongodb import get_db


class AssessmentMetadataRepositoryError(RuntimeError):
    """Raised when compact table metadata cannot be loaded."""


@dataclass(frozen=True, slots=True)
class TableAssessmentMetadata:
    table_id: str
    document_id: str
    title: str
    summary: str
    keywords: tuple[str, ...]


class AssessmentMetadataRepository(Protocol):
    async def load_table_metadata(
        self,
        *,
        user_id: str,
        document_ids: Sequence[str],
        table_ids: Sequence[str],
    ) -> dict[str, TableAssessmentMetadata]: ...


class MongoAssessmentMetadataRepository:
    """Read compact semantic metadata without loading or checkpointing rows."""

    async def load_table_metadata(
        self,
        *,
        user_id: str,
        document_ids: Sequence[str],
        table_ids: Sequence[str],
    ) -> dict[str, TableAssessmentMetadata]:
        unique_document_ids = tuple(dict.fromkeys(document_ids))
        unique_table_ids = tuple(dict.fromkeys(table_ids))
        if not user_id.strip() or not unique_document_ids:
            raise ValueError("user_id and document_ids are required")
        if not unique_table_ids:
            return {}
        try:
            documents = await get_db().structured_tables.find(
                {
                    "user_id": user_id,
                    "document_id": {"$in": list(unique_document_ids)},
                    "table_id": {"$in": list(unique_table_ids)},
                },
                {
                    "_id": 0,
                    "table_id": 1,
                    "document_id": 1,
                    "title": 1,
                    "summary": 1,
                    "short_summary": 1,
                    "deterministic_summary": 1,
                    "keywords": 1,
                },
            ).to_list(length=len(unique_table_ids))
        except Exception as exc:
            raise AssessmentMetadataRepositoryError(
                "table assessment metadata could not be loaded"
            ) from exc

        output: dict[str, TableAssessmentMetadata] = {}
        for document in documents:
            table_id = str(document.get("table_id") or "").strip()
            document_id = str(document.get("document_id") or "").strip()
            if not table_id or not document_id:
                continue
            summary = str(
                document.get("summary")
                or document.get("short_summary")
                or document.get("deterministic_summary")
                or ""
            ).strip()
            keywords = tuple(
                dict.fromkeys(
                    str(value).strip()
                    for value in document.get("keywords", [])
                    if str(value).strip()
                )
            )[:20]
            output[table_id] = TableAssessmentMetadata(
                table_id=table_id,
                document_id=document_id,
                title=str(document.get("title") or "").strip(),
                summary=summary,
                keywords=keywords,
            )
        return output
