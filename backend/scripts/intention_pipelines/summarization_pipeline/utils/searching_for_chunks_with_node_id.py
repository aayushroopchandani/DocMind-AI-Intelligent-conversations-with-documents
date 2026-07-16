from __future__ import annotations

import asyncio
import os
from math import inf
from typing import Any

from qdrant_client import models
from qdrant_manager import get_client

from .operation_on_nodes import get_node_scope
from db import crud


def _chunk_collection_name() -> str:
    collection_name = os.getenv("QDRANT_COLLECTION_NAME")
    if not collection_name:
        raise RuntimeError("QDRANT_COLLECTION_NAME is not configured")
    return collection_name

# LangChain-style Qdrant payload structure:
#
# {
#     "page_content": "...",
#     "metadata": {
#         "user_id": "...",
#         "doc_id": "...",
#         "node_id": "node_4",
#         "chunk_index": 12,
#         "page_number": 10,
#         "start_index": 2500
#     }
# }

CONTENT_KEY = "page_content"
METADATA_KEY = "metadata"

USER_ID_FIELD = f"{METADATA_KEY}.user_id"
DOC_ID_FIELD = f"{METADATA_KEY}.doc_id"
NODE_ID_FIELD = f"{METADATA_KEY}.node_id"

DEFAULT_SCROLL_PAGE_SIZE = 512
DEFAULT_RETRIEVE_BATCH_SIZE = 256


def _integer_or_infinity(value: Any) -> int | float:
    """
    Converts sortable metadata values to integers.

    Missing or invalid values are placed after valid values.
    """
    if value is None:
        return inf

    try:
        return int(value)
    except (TypeError, ValueError):
        return inf


def _scroll_chunk_records(
    *,
    scroll_filter: models.Filter,
    page_size: int,
) -> list[models.Record]:
    """Scroll Qdrant with the shared sync client used by LangChain."""
    all_records: list[models.Record] = []
    offset: Any = None
    qdrant_client = get_client()

    while True:
        records, next_offset = qdrant_client.scroll(
            collection_name=_chunk_collection_name(),
            scroll_filter=scroll_filter,
            limit=page_size,
            offset=offset,

            # Retrieve only what the summarization pipeline needs.
            with_payload=[
                CONTENT_KEY,
                METADATA_KEY,
            ],
            with_vectors=False,
        )

        all_records.extend(records)

        if next_offset is None:
            break

        # Defensive protection against an unexpected pagination loop.
        if next_offset == offset:
            raise RuntimeError(
                "Qdrant returned the same pagination offset twice"
            )

        offset = next_offset

    return all_records


def _point_id(value: str) -> str | int:
    """Restore numeric Qdrant point IDs that were serialized for MongoDB."""
    return int(value) if value.isdigit() else value


def _retrieve_chunk_records(
    *,
    chunk_ids: list[str],
    batch_size: int,
) -> list[models.Record]:
    qdrant_client = get_client()
    records: list[models.Record] = []
    for start in range(0, len(chunk_ids), batch_size):
        batch = chunk_ids[start : start + batch_size]
        records.extend(
            qdrant_client.retrieve(
                collection_name=_chunk_collection_name(),
                ids=[_point_id(chunk_id) for chunk_id in batch],
                with_payload=[CONTENT_KEY, METADATA_KEY],
                with_vectors=False,
            )
        )
    return records


async def searching_for_chunks_by_ids(
    chunk_ids: list[str],
    doc_id: str,
    user_id: str,
    *,
    batch_size: int = DEFAULT_RETRIEVE_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """Retrieve preselected chunks by ID and restore original document order."""
    if not doc_id or not user_id:
        raise ValueError("doc_id and user_id are required")
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    unique_ids = list(dict.fromkeys(str(chunk_id) for chunk_id in chunk_ids))
    if not unique_ids:
        return []

    records = await asyncio.to_thread(
        _retrieve_chunk_records,
        chunk_ids=unique_ids,
        batch_size=batch_size,
    )
    chunks: list[dict[str, Any]] = []
    for record in records:
        payload = record.payload or {}
        metadata = payload.get(METADATA_KEY)
        if not isinstance(metadata, dict):
            raise ValueError(
                f"Qdrant point '{record.id}' contains invalid metadata"
            )
        # The ID list lives in MongoDB, but ownership is still verified against
        # Qdrant payloads before any content reaches the LLM.
        if metadata.get("user_id") != user_id or metadata.get("doc_id") != doc_id:
            raise ValueError("Representative chunk ownership mismatch")
        chunks.append(
            {
                "id": record.id,
                "page_content": payload.get(CONTENT_KEY, ""),
                "metadata": metadata,
            }
        )

    chunks.sort(
        key=lambda chunk: (
            _integer_or_infinity(
                chunk["metadata"].get("document_chunk_index")
            ),
            _integer_or_infinity(chunk["metadata"].get("page_number")),
            _integer_or_infinity(chunk["metadata"].get("start_index")),
            str(chunk["id"]),
        )
    )
    return chunks


async def searching_for_chunks_with_node_id(
    node_id: str | None,
    doc_id: str,
    user_id: str,
    *,
    page_size: int = DEFAULT_SCROLL_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """
    Retrieve every chunk belonging to a document node and its descendants.

    When node_id is None, all chunks belonging to the document are returned.

    Returned chunks are ordered by:
        1. Node position in the document
        2. Chunk index inside the node
        3. Page number
        4. Character/start index
        5. Qdrant point ID as a deterministic fallback
    """

    if not doc_id:
        raise ValueError("doc_id is required")

    if not user_id:
        raise ValueError("user_id is required")

    if page_size <= 0:
        raise ValueError("page_size must be greater than zero")

    document = await crud.get_document_nodes(
        user_id=user_id,
        document_id=doc_id,
    )

    if document is None:
        raise ValueError(f"Document with id '{doc_id}' was not found")

    nodes: list[dict[str, Any]] = (
        document.get("nodes", {}).get("nodes", [])
    )

    if not nodes:
        raise ValueError(
            f"No nodes were found for document with id '{doc_id}'"
        )

    # None means retrieve the complete document.
    scope_node_ids = get_node_scope(
        nodes=nodes,
        node_id=node_id,
    )

    if scope_node_ids is None:
        ordered_node_ids = [
            node["node_id"]
            for node in nodes
            if node.get("node_id")
        ]
    else:
        ordered_node_ids = scope_node_ids

    # Remove accidental duplicates while preserving document order.
    ordered_node_ids = list(dict.fromkeys(ordered_node_ids))

    if not ordered_node_ids:
        return []

    node_order = {
        current_node_id: position
        for position, current_node_id in enumerate(ordered_node_ids)
    }

    filter_conditions: list[models.FieldCondition] = [
        models.FieldCondition(
            key=USER_ID_FIELD,
            match=models.MatchValue(value=user_id),
        ),
        models.FieldCondition(
            key=DOC_ID_FIELD,
            match=models.MatchValue(value=doc_id),
        ),
    ]

    # For section summarization, limit retrieval to the selected node
    # and all of its descendants.
    if scope_node_ids is not None:
        filter_conditions.append(
            models.FieldCondition(
                key=NODE_ID_FIELD,
                match=models.MatchAny(any=ordered_node_ids),
            )
        )

    scroll_filter = models.Filter(
        must=filter_conditions,
    )

    all_records = await asyncio.to_thread(
        _scroll_chunk_records,
        scroll_filter=scroll_filter,
        page_size=page_size,
    )

    chunks: list[dict[str, Any]] = []

    for record in all_records:
        payload = record.payload or {}
        metadata = payload.get(METADATA_KEY, {})

        if not isinstance(metadata, dict):
            raise ValueError(
                f"Qdrant point '{record.id}' contains invalid metadata"
            )

        chunk_node_id = metadata.get("node_id")

        if not chunk_node_id and scope_node_ids is not None:
            raise ValueError(
                f"Qdrant point '{record.id}' does not contain node_id"
            )

        chunks.append(
            {
                "id": record.id,
                "page_content": payload.get(CONTENT_KEY, ""),
                "metadata": metadata,
            }
        )

    if scope_node_ids is None:
        chunks.sort(
            key=lambda chunk: (
                _integer_or_infinity(
                    chunk["metadata"].get("document_chunk_index")
                ),
                _integer_or_infinity(
                    chunk["metadata"].get("page_number")
                ),
                _integer_or_infinity(
                    chunk["metadata"].get("start_index")
                ),
                str(chunk["id"]),
            )
        )
    else:
        unknown_node_position = len(node_order)
        chunks.sort(
            key=lambda chunk: (
                node_order.get(
                    chunk["metadata"].get("node_id"),
                    unknown_node_position,
                ),
                _integer_or_infinity(
                    chunk["metadata"].get("chunk_index")
                ),
                _integer_or_infinity(
                    chunk["metadata"].get("page_number")
                ),
                _integer_or_infinity(
                    chunk["metadata"].get("start_index")
                ),
                str(chunk["id"]),
            )
        )

    return chunks
