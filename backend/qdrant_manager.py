from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any, Final, Iterable

from dotenv import load_dotenv
from langchain_qdrant import QdrantVectorStore
from qdrant_client import AsyncQdrantClient, QdrantClient, models


load_dotenv()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CHUNK_VECTOR_SIZE: Final = 1536
NODE_VECTOR_SIZE: Final = 512

DEFAULT_DISTANCE: Final = models.Distance.COSINE

# These fields are used repeatedly in your Qdrant filters.
DEFAULT_PAYLOAD_INDEXES: Final[tuple[str, ...]] = (
    "metadata.user_id",
    "metadata.doc_id",
    "metadata.node_id",
)

VECTOR_STORE_CACHE_SIZE: Final = 16


# ---------------------------------------------------------------------------
# Qdrant connection configuration
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_connection_options() -> tuple[tuple[str, Any], ...]:
    """
    Resolve Qdrant configuration once per process.

    Priority:
        1. QDRANT_PATH
        2. QDRANT_URL
        3. QDRANT_HOST
        4. Default local path
    """

    qdrant_path = os.getenv("QDRANT_PATH")
    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")
    qdrant_host = os.getenv("QDRANT_HOST")
    qdrant_port = os.getenv("QDRANT_PORT")

    if qdrant_path:
        path = Path(qdrant_path)

        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path

        return (
            ("path", str(path.resolve())),
        )

    if qdrant_url:
        options: list[tuple[str, Any]] = [
            ("url", qdrant_url),
        ]

        if qdrant_api_key:
            options.append(
                ("api_key", qdrant_api_key)
            )

        return tuple(options)

    if qdrant_host:
        options = [
            ("host", qdrant_host),
            (
                "port",
                int(qdrant_port) if qdrant_port else 6333,
            ),
        ]

        if qdrant_api_key:
            options.append(
                ("api_key", qdrant_api_key)
            )

        return tuple(options)

    default_path = (
        Path(__file__).resolve().parent
        / "qdrant_storage"
    )

    return (
        ("path", str(default_path.resolve())),
    )


def _get_client_kwargs() -> dict[str, Any]:
    return dict(_get_connection_options())


def is_local_qdrant() -> bool:
    """
    True when Qdrant is running through embedded path mode.
    """
    return any(
        key == "path"
        for key, _ in _get_connection_options()
    )


# ---------------------------------------------------------------------------
# Cached Qdrant clients
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    """
    Return one shared synchronous Qdrant client.

    Used by LangChain QdrantVectorStore and synchronous code.
    """

    if (
        is_local_qdrant()
        and get_async_client.cache_info().currsize > 0
    ):
        raise RuntimeError(
            "Qdrant local path mode cannot keep synchronous "
            "and asynchronous clients open together. Use only "
            "one client type locally, or configure QDRANT_URL."
        )

    return QdrantClient(
        **_get_client_kwargs()
    )


@lru_cache(maxsize=1)
def get_async_client() -> AsyncQdrantClient:
    """
    Return one shared asynchronous Qdrant client.

    Use this for direct async Qdrant operations such as scroll,
    delete, retrieve, query_points, and upsert.
    """

    if (
        is_local_qdrant()
        and get_client.cache_info().currsize > 0
    ):
        raise RuntimeError(
            "Qdrant local path mode cannot keep synchronous "
            "and asynchronous clients open together. Use only "
            "one client type locally, or configure QDRANT_URL."
        )

    return AsyncQdrantClient(
        **_get_client_kwargs()
    )


# ---------------------------------------------------------------------------
# Distance handling
# ---------------------------------------------------------------------------

def _parse_distance(
    distance: str | models.Distance,
) -> models.Distance:
    if isinstance(distance, models.Distance):
        return distance

    distance_map = {
        "COSINE": models.Distance.COSINE,
        "EUCLID": models.Distance.EUCLID,
        "DOT": models.Distance.DOT,
    }

    normalized_distance = distance.upper()

    if normalized_distance not in distance_map:
        raise ValueError(
            f"Unsupported distance '{distance}'. "
            f"Supported values: {', '.join(distance_map)}"
        )

    return distance_map[normalized_distance]


# ---------------------------------------------------------------------------
# Basic collection functions
# ---------------------------------------------------------------------------

def collection_exists(
    collection_name: str,
    client: QdrantClient | None = None,
) -> bool:
    """
    Check the current Qdrant collection state.

    This result should not be cached because a collection can be
    created or deleted outside this process.
    """

    qdrant = client or get_client()

    return qdrant.collection_exists(
        collection_name=collection_name
    )


async def collection_exists_async(
    collection_name: str,
    client: AsyncQdrantClient | None = None,
) -> bool:
    """
    Async collection existence check.
    """

    qdrant = client or get_async_client()

    return await qdrant.collection_exists(
        collection_name=collection_name
    )


def create_collection(
    collection_name: str,
    vector_size: int = CHUNK_VECTOR_SIZE,
    distance: str | models.Distance = DEFAULT_DISTANCE,
    client: QdrantClient | None = None,
) -> None:
    """
    Create a collection using one unnamed dense vector.
    """

    if not collection_name:
        raise ValueError(
            "collection_name is required"
        )

    if vector_size <= 0:
        raise ValueError(
            "vector_size must be greater than zero"
        )

    qdrant = client or get_client()

    qdrant.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=vector_size,
            distance=_parse_distance(distance),
        ),
    )


async def create_collection_async(
    collection_name: str,
    vector_size: int = CHUNK_VECTOR_SIZE,
    distance: str | models.Distance = DEFAULT_DISTANCE,
    client: AsyncQdrantClient | None = None,
) -> None:
    """
    Asynchronously create a collection.
    """

    if not collection_name:
        raise ValueError(
            "collection_name is required"
        )

    if vector_size <= 0:
        raise ValueError(
            "vector_size must be greater than zero"
        )

    qdrant = client or get_async_client()

    await qdrant.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=vector_size,
            distance=_parse_distance(distance),
        ),
    )


# ---------------------------------------------------------------------------
# Collection validation and initialization
# ---------------------------------------------------------------------------

def _validate_collection(
    collection_name: str,
    *,
    vector_size: int,
    distance: models.Distance,
    client: QdrantClient,
) -> None:
    """
    Verify that an existing collection has the expected configuration.
    """

    collection = client.get_collection(
        collection_name=collection_name
    )

    vector_config = (
        collection.config.params.vectors
    )

    if isinstance(vector_config, dict):
        raise ValueError(
            f"Collection '{collection_name}' uses named "
            "vectors, but this manager expects one unnamed vector."
        )

    if vector_config.size != vector_size:
        raise ValueError(
            f"Collection '{collection_name}' has vector "
            f"size {vector_config.size}, but {vector_size} "
            "was requested. Use separate collections for "
            "chunk vectors and node vectors."
        )

    if vector_config.distance != distance:
        raise ValueError(
            f"Collection '{collection_name}' uses distance "
            f"{vector_config.distance}, but {distance} "
            "was requested."
        )


def _ensure_payload_indexes(
    collection_name: str,
    fields: Iterable[str],
    *,
    client: QdrantClient,
) -> None:
    """
    Create indexes for metadata fields frequently used in filters.
    """

    collection = client.get_collection(
        collection_name=collection_name
    )

    existing_schema = (
        collection.payload_schema or {}
    )

    for field_name in fields:
        if field_name in existing_schema:
            continue

        client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=(
                models.PayloadSchemaType.KEYWORD
            ),
            wait=True,
        )


def _ensure_collection_impl(
    collection_name: str,
    *,
    vector_size: int,
    distance: models.Distance,
    payload_indexes: tuple[str, ...],
    client: QdrantClient,
) -> None:
    """
    Actual collection creation and validation implementation.
    """

    if not client.collection_exists(
        collection_name=collection_name
    ):
        try:
            client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=distance,
                ),
            )

        except Exception:
            # Another Uvicorn worker may have created it
            # between collection_exists and create_collection.
            if not client.collection_exists(
                collection_name=collection_name
            ):
                raise

    _validate_collection(
        collection_name,
        vector_size=vector_size,
        distance=distance,
        client=client,
    )

    if payload_indexes:
        _ensure_payload_indexes(
            collection_name,
            payload_indexes,
            client=client,
        )


@lru_cache(maxsize=32)
def _ensure_default_collection_once(
    collection_name: str,
    vector_size: int,
    distance_name: str,
    payload_indexes: tuple[str, ...],
) -> None:
    """
    Perform collection initialization only once per process.

    This cache is only used with the default shared client.
    """

    _ensure_collection_impl(
        collection_name,
        vector_size=vector_size,
        distance=_parse_distance(distance_name),
        payload_indexes=payload_indexes,
        client=get_client(),
    )


def ensure_collection(
    collection_name: str,
    *,
    vector_size: int,
    distance: str | models.Distance = DEFAULT_DISTANCE,
    payload_indexes: Iterable[str] = DEFAULT_PAYLOAD_INDEXES,
    client: QdrantClient | None = None,
) -> None:
    """
    Ensure that a collection exists and has the correct vector size.

    With the default shared client, this initialization is cached.
    """

    if not collection_name:
        raise ValueError(
            "collection_name is required"
        )

    if vector_size <= 0:
        raise ValueError(
            "vector_size must be greater than zero"
        )

    parsed_distance = _parse_distance(distance)

    indexes = tuple(
        dict.fromkeys(payload_indexes)
    )

    if client is None:
        _ensure_default_collection_once(
            collection_name,
            vector_size,
            parsed_distance.name,
            indexes,
        )
        return

    _ensure_collection_impl(
        collection_name,
        vector_size=vector_size,
        distance=parsed_distance,
        payload_indexes=indexes,
        client=client,
    )


# ---------------------------------------------------------------------------
# Bounded vector-store cache
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _VectorStoreCacheEntry:
    client: QdrantClient
    embedding: Any
    vector_store: QdrantVectorStore


_vector_store_cache: OrderedDict[
    tuple[str, int, int],
    _VectorStoreCacheEntry,
] = OrderedDict()

_vector_store_cache_lock = RLock()


def get_vector_store(
    collection_name: str,
    embedding: Any,
    client: QdrantClient | None = None,
) -> QdrantVectorStore:
    """
    Return a cached LangChain QdrantVectorStore.

    The cache uses embedding object identity because embedding
    instances are not guaranteed to be hashable.
    """

    if not collection_name:
        raise ValueError(
            "collection_name is required"
        )

    if embedding is None:
        raise ValueError(
            "embedding is required"
        )

    qdrant = client or get_client()

    cache_key = (
        collection_name,
        id(embedding),
        id(qdrant),
    )

    with _vector_store_cache_lock:
        cached_entry = _vector_store_cache.get(
            cache_key
        )

        if (
            cached_entry is not None
            and cached_entry.embedding is embedding
            and cached_entry.client is qdrant
        ):
            _vector_store_cache.move_to_end(
                cache_key
            )

            return cached_entry.vector_store

        vector_store = QdrantVectorStore(
            client=qdrant,
            collection_name=collection_name,
            embedding=embedding,
        )

        _vector_store_cache[cache_key] = (
            _VectorStoreCacheEntry(
                client=qdrant,
                embedding=embedding,
                vector_store=vector_store,
            )
        )

        _vector_store_cache.move_to_end(
            cache_key
        )

        while (
            len(_vector_store_cache)
            > VECTOR_STORE_CACHE_SIZE
        ):
            _vector_store_cache.popitem(
                last=False
            )

        return vector_store


def get_or_create_vector_store(
    collection_name: str,
    embedding: Any,
    vector_size: int,
    distance: str | models.Distance = DEFAULT_DISTANCE,
    client: QdrantClient | None = None,
) -> QdrantVectorStore:
    """
    Ensure the collection once and return a cached vector store.
    """

    qdrant = client or get_client()

    ensure_collection(
        collection_name,
        vector_size=vector_size,
        distance=distance,

        # Use the cached initialization path when the default
        # manager client is being used.
        client=None if client is None else qdrant,
    )

    return get_vector_store(
        collection_name=collection_name,
        embedding=embedding,
        client=qdrant,
    )


# ---------------------------------------------------------------------------
# Chunk and node vector stores
# ---------------------------------------------------------------------------

def get_chunk_vector_store(
    embedding: Any,
    collection_name: str | None = None,
) -> QdrantVectorStore:
    """
    Return the vector store for 1536-dimensional document chunks.
    """

    name = (
        collection_name
        or os.getenv("QDRANT_COLLECTION_NAME")
    )

    if not name:
        raise RuntimeError(
            "QDRANT_COLLECTION_NAME is not configured"
        )

    return get_or_create_vector_store(
        collection_name=name,
        embedding=embedding,
        vector_size=CHUNK_VECTOR_SIZE,
    )


def get_node_vector_store(
    embedding: Any,
    collection_name: str | None = None,
) -> QdrantVectorStore:
    """
    Return the vector store for 512-dimensional document nodes.
    """

    name = (
        collection_name
        or os.getenv(
            "QDRANT_NODES_COLLECTION_NAME"
        )
        or os.getenv(
            "QDRANT_COLLECTION_NAME_NODES"
        )
    )

    if not name:
        raise RuntimeError(
            "QDRANT_NODES_COLLECTION_NAME or "
            "QDRANT_COLLECTION_NAME_NODES is not configured"
        )

    return get_or_create_vector_store(
        collection_name=name,
        embedding=embedding,
        vector_size=NODE_VECTOR_SIZE,
    )


# ---------------------------------------------------------------------------
# Delete document vectors
# ---------------------------------------------------------------------------

def _document_filter(
    *,
    user_id: str,
    document_id: str,
) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(
                key="metadata.user_id",
                match=models.MatchValue(
                    value=user_id
                ),
            ),
            models.FieldCondition(
                key="metadata.doc_id",
                match=models.MatchValue(
                    value=document_id
                ),
            ),
        ]
    )


def delete_document_vectors(
    collection_name: str,
    *,
    user_id: str,
    document_id: str,
    client: QdrantClient | None = None,
) -> None:
    """
    Delete every vector belonging to one user's document.
    """

    qdrant = client or get_client()

    if not qdrant.collection_exists(
        collection_name=collection_name
    ):
        return

    qdrant.delete(
        collection_name=collection_name,
        points_selector=models.FilterSelector(
            filter=_document_filter(
                user_id=user_id,
                document_id=document_id,
            )
        ),
        wait=True,
    )


async def delete_document_vectors_async(
    collection_name: str,
    *,
    user_id: str,
    document_id: str,
    client: AsyncQdrantClient | None = None,
) -> None:
    """
    Asynchronously delete every vector belonging to a document.
    """

    qdrant = client or get_async_client()

    if not await qdrant.collection_exists(
        collection_name=collection_name
    ):
        return

    await qdrant.delete(
        collection_name=collection_name,
        points_selector=models.FilterSelector(
            filter=_document_filter(
                user_id=user_id,
                document_id=document_id,
            )
        ),
        wait=True,
    )


# ---------------------------------------------------------------------------
# Cache cleanup
# ---------------------------------------------------------------------------

def clear_runtime_caches() -> None:
    """
    Clear collection-setup and vector-store caches.

    The Qdrant client connections remain open.
    """

    _ensure_default_collection_once.cache_clear()

    with _vector_store_cache_lock:
        _vector_store_cache.clear()
