from __future__ import annotations

import hashlib
import math
import os
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

from qdrant_client import QdrantClient, models

import qdrant_manager


SPARSE_VECTOR_NAME: Final = "lexical"
_TOKEN_RE = re.compile(r"[^\W_]+(?:[.\-'’][^\W_]+)*", re.UNICODE)


def text_sparse_collection_name(dense_collection: str | None = None) -> str:
    dense = dense_collection or os.getenv("QDRANT_COLLECTION_NAME")
    if not dense:
        raise RuntimeError("QDRANT_COLLECTION_NAME is not configured")
    return os.getenv(
        "DATA_ANALYSIS_TEXT_SPARSE_COLLECTION",
        f"{dense}__sparse",
    )


def table_sparse_collection_name() -> str:
    return os.getenv(
        "DATA_ANALYSIS_TABLE_SPARSE_COLLECTION",
        "structured_tables__sparse",
    )


@dataclass(frozen=True, slots=True)
class SparseRecord:
    point_id: models.ExtendedPointId
    text: str
    payload: dict[str, Any]


class HashedLexicalEncoder:
    """Dependency-free sparse term encoder paired with Qdrant's IDF modifier."""

    @staticmethod
    def _terms(text: str) -> list[str]:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        return _TOKEN_RE.findall(normalized)

    @staticmethod
    def _index(term: str) -> int:
        return int.from_bytes(
            hashlib.blake2s(term.encode("utf-8"), digest_size=4).digest(),
            "big",
        )

    def encode(self, text: str) -> models.SparseVector:
        counts = Counter(self._index(term) for term in self._terms(text))
        indices = sorted(counts)
        return models.SparseVector(
            indices=indices,
            values=[1.0 + math.log(counts[index]) for index in indices],
        )

    def encode_many(self, texts: Sequence[str]) -> list[models.SparseVector]:
        return [self.encode(text) for text in texts]


@lru_cache(maxsize=1)
def get_sparse_encoder() -> HashedLexicalEncoder:
    return HashedLexicalEncoder()


def ensure_sparse_collection(
    collection_name: str,
    *,
    payload_indexes: Iterable[str],
    client: QdrantClient | None = None,
) -> None:
    qdrant = client or qdrant_manager.get_client()
    if not qdrant.collection_exists(collection_name=collection_name):
        try:
            qdrant.create_collection(
                collection_name=collection_name,
                vectors_config={},
                sparse_vectors_config={
                    SPARSE_VECTOR_NAME: models.SparseVectorParams(
                        modifier=models.Modifier.IDF
                    )
                },
            )
        except Exception:
            # Another API worker may have created the companion collection.
            if not qdrant.collection_exists(collection_name=collection_name):
                raise

    collection = qdrant.get_collection(collection_name=collection_name)
    sparse_vectors = collection.config.params.sparse_vectors or {}
    if SPARSE_VECTOR_NAME not in sparse_vectors:
        raise RuntimeError(
            f"Collection {collection_name!r} does not contain the required "
            f"{SPARSE_VECTOR_NAME!r} sparse vector"
        )

    existing_schema = collection.payload_schema or {}
    for field in dict.fromkeys(payload_indexes):
        if field not in existing_schema:
            qdrant.create_payload_index(
                collection_name=collection_name,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
                wait=True,
            )


def upsert_sparse_records(
    collection_name: str,
    records: Sequence[SparseRecord],
    *,
    payload_indexes: Iterable[str],
    client: QdrantClient | None = None,
    ensure: bool = True,
) -> int:
    if not records:
        return 0
    qdrant = client or qdrant_manager.get_client()
    if ensure:
        ensure_sparse_collection(
            collection_name,
            payload_indexes=payload_indexes,
            client=qdrant,
        )
    vectors = get_sparse_encoder().encode_many([record.text for record in records])
    qdrant.upsert(
        collection_name=collection_name,
        points=[
            models.PointStruct(
                id=record.point_id,
                vector={SPARSE_VECTOR_NAME: vector},
                payload=record.payload,
            )
            for record, vector in zip(records, vectors, strict=True)
        ],
        wait=True,
    )
    return len(records)


def delete_sparse_by_filter(
    collection_name: str,
    query_filter: models.Filter,
    *,
    client: QdrantClient | None = None,
) -> None:
    qdrant = client or qdrant_manager.get_client()
    if not qdrant.collection_exists(collection_name=collection_name):
        return
    qdrant.delete(
        collection_name=collection_name,
        points_selector=models.FilterSelector(filter=query_filter),
        wait=True,
    )


def delete_sparse_ids(
    collection_name: str,
    point_ids: Sequence[models.ExtendedPointId],
    *,
    client: QdrantClient | None = None,
) -> None:
    if not point_ids:
        return
    qdrant = client or qdrant_manager.get_client()
    if not qdrant.collection_exists(collection_name=collection_name):
        return
    qdrant.delete(
        collection_name=collection_name,
        points_selector=models.PointIdsList(points=list(point_ids)),
        wait=True,
    )
