from __future__ import annotations

import os
from dataclasses import dataclass

from qdrant_client import QdrantClient, models

import qdrant_manager

from .collections import STRUCTURED_TABLES_COLLECTION, TABLE_PAYLOAD_INDEXES
from .sparse_index import (
    SparseRecord,
    ensure_sparse_collection,
    table_sparse_collection_name,
    text_sparse_collection_name,
    upsert_sparse_records,
)


DEFAULT_BACKFILL_BATCH_SIZE = 256


@dataclass(frozen=True, slots=True)
class SparseBackfillSpec:
    """Describe how one dense collection maps to its sparse companion."""

    source_collection: str
    target_collection: str
    text_payload_field: str
    payload_indexes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.source_collection.strip():
            raise ValueError("source_collection must not be empty")
        if not self.target_collection.strip():
            raise ValueError("target_collection must not be empty")
        if self.source_collection == self.target_collection:
            raise ValueError("source and target collections must be different")
        if not self.text_payload_field.strip():
            raise ValueError("text_payload_field must not be empty")


@dataclass(frozen=True, slots=True)
class SparseBackfillResult:
    source_collection: str
    target_collection: str
    scanned_count: int
    indexed_count: int
    skipped_count: int
    batch_count: int


def data_analysis_backfill_specs(
    text_collection: str | None = None,
) -> tuple[SparseBackfillSpec, SparseBackfillSpec]:
    """Return the text and table mappings used by hybrid retrieval."""

    selected_text_collection = (
        text_collection or os.getenv("QDRANT_COLLECTION_NAME") or ""
    ).strip()
    if not selected_text_collection:
        raise RuntimeError("QDRANT_COLLECTION_NAME is not configured")
    return (
        SparseBackfillSpec(
            source_collection=selected_text_collection,
            target_collection=text_sparse_collection_name(
                selected_text_collection
            ),
            text_payload_field="page_content",
            payload_indexes=tuple(qdrant_manager.DEFAULT_PAYLOAD_INDEXES),
        ),
        SparseBackfillSpec(
            source_collection=STRUCTURED_TABLES_COLLECTION,
            target_collection=table_sparse_collection_name(),
            text_payload_field="summary",
            payload_indexes=TABLE_PAYLOAD_INDEXES,
        ),
    )


def _sparse_record(
    point: models.Record,
    *,
    text_payload_field: str,
) -> SparseRecord | None:
    payload = point.payload
    if not isinstance(payload, dict):
        return None
    text = payload.get(text_payload_field)
    if not isinstance(text, str) or not text.strip():
        return None
    return SparseRecord(
        point_id=point.id,
        text=text,
        payload=dict(payload),
    )


def backfill_sparse_collection(
    spec: SparseBackfillSpec,
    *,
    client: QdrantClient | None = None,
    batch_size: int = DEFAULT_BACKFILL_BATCH_SIZE,
    source_filter: models.Filter | None = None,
) -> SparseBackfillResult:
    """Stream existing payloads into a lexical sparse companion collection."""

    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")
    qdrant = client or qdrant_manager.get_client()
    if not qdrant.collection_exists(collection_name=spec.source_collection):
        raise RuntimeError(
            f"Source collection {spec.source_collection!r} does not exist"
        )
    ensure_sparse_collection(
        spec.target_collection,
        payload_indexes=spec.payload_indexes,
        client=qdrant,
    )

    scanned_count = 0
    indexed_count = 0
    skipped_count = 0
    batch_count = 0
    offset: models.ExtendedPointId | None = None
    while True:
        points, next_offset = qdrant.scroll(
            collection_name=spec.source_collection,
            scroll_filter=source_filter,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if points:
            batch_count += 1
        scanned_count += len(points)
        records = [
            record
            for point in points
            if (
                record := _sparse_record(
                    point,
                    text_payload_field=spec.text_payload_field,
                )
            )
            is not None
        ]
        skipped_count += len(points) - len(records)
        indexed_count += upsert_sparse_records(
            spec.target_collection,
            records,
            payload_indexes=spec.payload_indexes,
            client=qdrant,
            ensure=False,
        )

        if next_offset is None:
            break
        if next_offset == offset:
            raise RuntimeError("Qdrant returned the same pagination offset twice")
        offset = next_offset

    return SparseBackfillResult(
        source_collection=spec.source_collection,
        target_collection=spec.target_collection,
        scanned_count=scanned_count,
        indexed_count=indexed_count,
        skipped_count=skipped_count,
        batch_count=batch_count,
    )


def backfill_data_analysis_sparse_indexes(
    *,
    client: QdrantClient | None = None,
    batch_size: int = DEFAULT_BACKFILL_BATCH_SIZE,
    text_collection: str | None = None,
) -> tuple[SparseBackfillResult, SparseBackfillResult]:
    """Backfill both sparse indexes required by the data-analysis agent."""

    qdrant = client or qdrant_manager.get_client()
    text_spec, table_spec = data_analysis_backfill_specs(text_collection)
    return (
        backfill_sparse_collection(
            text_spec,
            client=qdrant,
            batch_size=batch_size,
        ),
        backfill_sparse_collection(
            table_spec,
            client=qdrant,
            batch_size=batch_size,
        ),
    )
