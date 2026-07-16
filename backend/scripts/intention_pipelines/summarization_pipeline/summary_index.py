from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from math import inf
from typing import Any

import numpy as np
from qdrant_client import models

from db import crud
from qdrant_manager import get_client

from .representative_selector import select_representative_indices


logger = logging.getLogger(__name__)

SUMMARY_INDEX_VERSION = "v1"
DEFAULT_SCROLL_PAGE_SIZE = 512
METADATA_KEY = "metadata"


def _chunk_collection_name() -> str:
    collection_name = os.getenv("QDRANT_COLLECTION_NAME")
    if not collection_name:
        raise RuntimeError("QDRANT_COLLECTION_NAME is not configured")
    return collection_name


def _integer_or_infinity(value: Any) -> int | float:
    try:
        return int(value)
    except (TypeError, ValueError):
        return inf


def initialize_pending_summary_indexes(
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return outline nodes initialized for an asynchronous v1 index build."""
    initialized: list[dict[str, Any]] = []
    for node in nodes:
        current = dict(node)
        current["summary_index"] = {
            "status": "pending",
            "chunk_count": 0,
            "representative_chunk_ids": [],
            "method": "pending",
            "cluster_count": 0,
            "version": SUMMARY_INDEX_VERSION,
        }
        initialized.append(current)
    return initialized


def _dense_vector(record: models.Record) -> np.ndarray:
    vector = record.vector
    if isinstance(vector, dict):
        if len(vector) != 1:
            raise ValueError(
                f"Qdrant point '{record.id}' does not have one dense vector"
            )
        vector = next(iter(vector.values()))

    if vector is None:
        raise ValueError(f"Qdrant point '{record.id}' has no dense vector")
    dense = np.asarray(vector, dtype=np.float32)
    if dense.ndim != 1 or dense.size == 0:
        raise ValueError(f"Qdrant point '{record.id}' has an invalid dense vector")
    return dense


def _scroll_embedded_chunks(
    *,
    user_id: str,
    document_id: str,
    page_size: int = DEFAULT_SCROLL_PAGE_SIZE,
) -> list[tuple[str, dict[str, Any], np.ndarray]]:
    qdrant = get_client()
    scroll_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="metadata.user_id",
                match=models.MatchValue(value=user_id),
            ),
            models.FieldCondition(
                key="metadata.doc_id",
                match=models.MatchValue(value=document_id),
            ),
        ]
    )

    chunks: list[tuple[str, dict[str, Any], np.ndarray]] = []
    offset: Any = None
    while True:
        records, next_offset = qdrant.scroll(
            collection_name=_chunk_collection_name(),
            scroll_filter=scroll_filter,
            limit=page_size,
            offset=offset,
            with_payload=[METADATA_KEY],
            with_vectors=True,
        )
        for record in records:
            payload = record.payload or {}
            metadata = payload.get(METADATA_KEY)
            if not isinstance(metadata, dict):
                raise ValueError(
                    f"Qdrant point '{record.id}' contains invalid metadata"
                )
            chunks.append((str(record.id), metadata, _dense_vector(record)))

        if next_offset is None:
            break
        if next_offset == offset:
            raise RuntimeError("Qdrant returned the same pagination offset twice")
        offset = next_offset

    chunks.sort(
        key=lambda item: (
            _integer_or_infinity(item[1].get("document_chunk_index")),
            _integer_or_infinity(item[1].get("page_number")),
            _integer_or_infinity(item[1].get("start_index")),
            item[0],
        )
    )
    return chunks


def _build_node_summary_indexes(
    *,
    nodes: list[dict[str, Any]],
    embedded_chunks: list[tuple[str, dict[str, Any], np.ndarray]],
) -> list[dict[str, Any]]:
    chunks_by_node: dict[str, list[tuple[str, np.ndarray]]] = defaultdict(list)
    known_node_ids = {
        str(node["node_id"])
        for node in nodes
        if node.get("node_id") is not None
    }

    for chunk_id, metadata, embedding in embedded_chunks:
        node_id = metadata.get("node_id")
        if node_id is None or str(node_id) not in known_node_ids:
            logger.warning(
                "Skipping summary-index chunk %s with unknown node_id=%r",
                chunk_id,
                node_id,
            )
            continue
        chunks_by_node[str(node_id)].append((chunk_id, embedding))

    indexed_nodes: list[dict[str, Any]] = []
    for node in nodes:
        current = dict(node)
        node_id = str(current.get("node_id"))
        node_chunks = chunks_by_node.get(node_id, [])

        if node_chunks:
            dimensions = {embedding.shape for _, embedding in node_chunks}
            if len(dimensions) != 1:
                raise ValueError(f"Node '{node_id}' has inconsistent vector dimensions")
            matrix = np.vstack([embedding for _, embedding in node_chunks])
            selection = select_representative_indices(matrix)
            representative_ids = [
                node_chunks[index][0]
                for index in selection.indices
            ]
            method = selection.method
            cluster_count = selection.cluster_count
        else:
            representative_ids = []
            method = "no_chunks"
            cluster_count = 0

        current["summary_index"] = {
            "status": "ready",
            "chunk_count": len(node_chunks),
            "representative_chunk_ids": representative_ids,
            "method": method,
            "cluster_count": cluster_count,
            "version": SUMMARY_INDEX_VERSION,
        }
        indexed_nodes.append(current)

    return indexed_nodes


async def build_summary_index(*, user_id: str, document_id: str) -> bool:
    """
    Idempotently build one document's representative summary index.

    MongoDB atomically claims pending/failed work. A ready index at the current
    version is never clustered again.
    """
    claimed = await crud.claim_summary_index_build(
        user_id=user_id,
        document_id=document_id,
        version=SUMMARY_INDEX_VERSION,
    )
    if not claimed:
        return False

    try:
        document = await crud.get_document_nodes(
            user_id=user_id,
            document_id=document_id,
        )
        if document is None:
            raise ValueError("Document not found")
        nodes = document.get("nodes", {}).get("nodes", [])
        if not nodes:
            raise ValueError("Document outline is empty")

        embedded_chunks = await asyncio.to_thread(
            _scroll_embedded_chunks,
            user_id=user_id,
            document_id=document_id,
        )
        indexed_nodes = await asyncio.to_thread(
            _build_node_summary_indexes,
            nodes=nodes,
            embedded_chunks=embedded_chunks,
        )
        completed = await crud.complete_summary_index_build(
            user_id=user_id,
            document_id=document_id,
            version=SUMMARY_INDEX_VERSION,
            nodes=indexed_nodes,
        )
        if not completed:
            raise RuntimeError("Summary-index build lost its MongoDB claim")
        return True
    except Exception as exc:
        await crud.mark_summary_index_failed(
            user_id=user_id,
            document_id=document_id,
            version=SUMMARY_INDEX_VERSION,
            error=str(exc),
        )
        raise
