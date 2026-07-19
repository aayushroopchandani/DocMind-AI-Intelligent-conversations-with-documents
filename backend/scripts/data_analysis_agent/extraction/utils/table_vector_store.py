from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import models

import qdrant_manager
from db.models.structured_table import StructuredTable
from utils.embeddings import get_chunk_embedding


STRUCTURED_TABLES_COLLECTION: Final = "structured_tables"
TABLE_VECTOR_SIZE: Final = 1536
TABLE_PAYLOAD_INDEXES: Final = (
    "content_type",
    "table_id",
    "document_id",
    "user_id",
    "chat_id",
    "node_id",
)


def table_discovery_payload(table: StructuredTable) -> dict[str, Any]:
    """Build Qdrant discovery metadata without including any table rows."""
    payload: dict[str, Any] = {
        "content_type": "table_summary",
        "table_id": table.table_id,
        "document_id": table.document_id,
        "user_id": table.user_id,
        "title": table.title,
        "extraction_method": table.extraction_method,
        "summary": table.summary,
        "columns": [column.key for column in table.columns],
        "metrics": [
            column.key for column in table.columns if column.type == "number"
        ],
        "units": list(
            dict.fromkeys(column.unit for column in table.columns if column.unit)
        ),
        "keywords": table.keywords,
        "row_count": len(table.rows),
        "page_start": table.page_start,
        "page_end": table.page_end,
    }
    if table.chat_id:
        payload["chat_id"] = table.chat_id
    if table.node_id:
        payload["node_id"] = table.node_id
    return payload


def _document_filter(*, user_id: str, document_id: str) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(
                key="user_id", match=models.MatchValue(value=user_id)
            ),
            models.FieldCondition(
                key="document_id", match=models.MatchValue(value=document_id)
            ),
        ]
    )


def delete_table_vectors(*, user_id: str, document_id: str) -> None:
    client = qdrant_manager.get_client()
    if not client.collection_exists(collection_name=STRUCTURED_TABLES_COLLECTION):
        return
    client.delete(
        collection_name=STRUCTURED_TABLES_COLLECTION,
        points_selector=models.FilterSelector(
            filter=_document_filter(user_id=user_id, document_id=document_id)
        ),
        wait=True,
    )


def _point_id(*, user_id: str, table_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"docmind:table:{user_id}:{table_id}"))


def delete_table_vectors_by_ids(*, user_id: str, table_ids: Sequence[str]) -> int:
    if not table_ids:
        return 0
    client = qdrant_manager.get_client()
    if not client.collection_exists(collection_name=STRUCTURED_TABLES_COLLECTION):
        return 0
    point_ids = [_point_id(user_id=user_id, table_id=table_id) for table_id in table_ids]
    client.delete(
        collection_name=STRUCTURED_TABLES_COLLECTION,
        points_selector=models.PointIdsList(points=point_ids),
        wait=True,
    )
    return len(point_ids)


def upsert_table_summaries(tables: Sequence[StructuredTable]) -> int:
    """Embed and upsert only new/replaced tables during fallback enrichment."""
    if not tables:
        return 0
    user_ids = {table.user_id for table in tables}
    document_ids = {table.document_id for table in tables}
    if len(user_ids) != 1 or len(document_ids) != 1:
        raise ValueError("A table indexing batch must belong to one user and document")
    if any(not table.summary for table in tables):
        raise ValueError("Every table must have a combined summary before indexing")

    vectors = get_chunk_embedding().embed_documents([table.summary for table in tables])
    if any(len(vector) != TABLE_VECTOR_SIZE for vector in vectors):
        raise ValueError(
            f"Table summary embeddings must be {TABLE_VECTOR_SIZE}-dimensional"
        )
    qdrant_manager.ensure_collection(
        STRUCTURED_TABLES_COLLECTION,
        vector_size=TABLE_VECTOR_SIZE,
        payload_indexes=TABLE_PAYLOAD_INDEXES,
    )
    points = [
        models.PointStruct(
            id=_point_id(user_id=table.user_id, table_id=table.table_id),
            vector=vector,
            payload=table_discovery_payload(table),
        )
        for table, vector in zip(tables, vectors, strict=True)
    ]
    qdrant_manager.get_client().upsert(
        collection_name=STRUCTURED_TABLES_COLLECTION,
        points=points,
        wait=True,
    )
    return len(points)


def index_table_summaries(tables: Sequence[StructuredTable]) -> int:
    """Embed combined summaries and idempotently replace one document's points."""
    if not tables:
        return 0
    user_ids = {table.user_id for table in tables}
    document_ids = {table.document_id for table in tables}
    if len(user_ids) != 1 or len(document_ids) != 1:
        raise ValueError("A table indexing batch must belong to one user and document")
    if any(not table.summary for table in tables):
        raise ValueError("Every table must have a combined summary before indexing")

    # Finish all remote embedding calls before replacing existing points. If an
    # embedding request fails, the previous complete index stays available.
    vectors = get_chunk_embedding().embed_documents([table.summary for table in tables])
    if any(len(vector) != TABLE_VECTOR_SIZE for vector in vectors):
        raise ValueError(
            f"Table summary embeddings must be {TABLE_VECTOR_SIZE}-dimensional"
        )

    client = qdrant_manager.get_client()
    qdrant_manager.ensure_collection(
        STRUCTURED_TABLES_COLLECTION,
        vector_size=TABLE_VECTOR_SIZE,
        payload_indexes=TABLE_PAYLOAD_INDEXES,
    )
    user_id = next(iter(user_ids))
    document_id = next(iter(document_ids))
    client.delete(
        collection_name=STRUCTURED_TABLES_COLLECTION,
        points_selector=models.FilterSelector(
            filter=_document_filter(user_id=user_id, document_id=document_id)
        ),
        wait=True,
    )
    points = [
        models.PointStruct(
            id=_point_id(user_id=table.user_id, table_id=table.table_id),
            vector=vector,
            payload=table_discovery_payload(table),
        )
        for table, vector in zip(tables, vectors, strict=True)
    ]
    client.upsert(
        collection_name=STRUCTURED_TABLES_COLLECTION,
        points=points,
        wait=True,
    )
    return len(points)
