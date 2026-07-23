from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import models

import qdrant_manager
from db.models.structured_table import StructuredTable
from scripts.data_analysis_agent.retrieval.utils.collections import (
    STRUCTURED_TABLES_COLLECTION,
    TABLE_PAYLOAD_INDEXES,
)
from scripts.data_analysis_agent.retrieval.utils.sparse_index import (
    SparseRecord,
    delete_sparse_by_filter,
    delete_sparse_ids,
    table_sparse_collection_name,
    upsert_sparse_records,
)
from utils.embeddings import get_chunk_embedding


TABLE_VECTOR_SIZE: Final = 1536
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
    query_filter = _document_filter(user_id=user_id, document_id=document_id)
    if client.collection_exists(collection_name=STRUCTURED_TABLES_COLLECTION):
        client.delete(
            collection_name=STRUCTURED_TABLES_COLLECTION,
            points_selector=models.FilterSelector(filter=query_filter),
            wait=True,
        )
    delete_sparse_by_filter(
        table_sparse_collection_name(),
        query_filter,
        client=client,
    )


def _point_id(*, user_id: str, table_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"docmind:table:{user_id}:{table_id}"))


def delete_table_vectors_by_ids(*, user_id: str, table_ids: Sequence[str]) -> int:
    if not table_ids:
        return 0
    client = qdrant_manager.get_client()
    point_ids = [_point_id(user_id=user_id, table_id=table_id) for table_id in table_ids]
    if not client.collection_exists(collection_name=STRUCTURED_TABLES_COLLECTION):
        delete_sparse_ids(
            table_sparse_collection_name(),
            point_ids,
            client=client,
        )
        return len(point_ids)
    client.delete(
        collection_name=STRUCTURED_TABLES_COLLECTION,
        points_selector=models.PointIdsList(points=point_ids),
        wait=True,
    )
    delete_sparse_ids(
        table_sparse_collection_name(),
        point_ids,
        client=client,
    )
    return len(point_ids)


def _sparse_records(tables: Sequence[StructuredTable]) -> list[SparseRecord]:
    return [
        SparseRecord(
            point_id=_point_id(user_id=table.user_id, table_id=table.table_id),
            text=table.summary,
            payload=table_discovery_payload(table),
        )
        for table in tables
    ]


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
    upsert_sparse_records(
        table_sparse_collection_name(),
        _sparse_records(tables),
        payload_indexes=TABLE_PAYLOAD_INDEXES,
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
    sparse_collection = table_sparse_collection_name()
    delete_sparse_by_filter(
        sparse_collection,
        _document_filter(user_id=user_id, document_id=document_id),
        client=client,
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
    upsert_sparse_records(
        sparse_collection,
        _sparse_records(tables),
        payload_indexes=TABLE_PAYLOAD_INDEXES,
        client=client,
    )
    return len(points)
