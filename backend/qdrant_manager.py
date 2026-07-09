import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models
from langchain_qdrant import QdrantVectorStore

load_dotenv()

def get_client() -> QdrantClient:
    """
    Connect to Qdrant based on environment variables or fallback to a local on-disk DB.
    """
    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")
    qdrant_host = os.getenv("QDRANT_HOST")
    qdrant_port = os.getenv("QDRANT_PORT")
    qdrant_path = os.getenv("QDRANT_PATH")

    # An explicit local path must take precedence over host settings. This also
    # prevents stale QDRANT_HOST/QDRANT_PORT values inherited by the Uvicorn
    # process from forcing a connection to a server that is not running.
    if qdrant_path:
        path = Path(qdrant_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        return QdrantClient(path=str(path.resolve()))
    elif qdrant_url:
        return QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
    elif qdrant_host:
        port = int(qdrant_port) if qdrant_port else 6333
        return QdrantClient(host=qdrant_host, port=port, api_key=qdrant_api_key)
    else:
        # Default to local path mode
        # If QDRANT_PATH isn't specified, use 'qdrant_storage' relative to backend directory
        base_dir = Path(__file__).resolve().parent
        return QdrantClient(path=str(base_dir / "qdrant_storage"))

def collection_exists(collection_name: str, client: Optional[QdrantClient] = None) -> bool:
    """
    Check if a collection exists.
    """
    if client is None:
        client = get_client()
    return client.collection_exists(collection_name=collection_name)

def create_collection(
    collection_name: str,
    vector_size: int = 1536,
    distance: str = "COSINE",
    client: Optional[QdrantClient] = None
) -> None:
    """
    Create a new collection with the given vector parameters.
    """
    if client is None:
        client = get_client()
        
    # Convert string distance to models.Distance
    distance_map = {
        "COSINE": models.Distance.COSINE,
        "EUCLID": models.Distance.EUCLID,
        "DOT": models.Distance.DOT,
    }
    qdrant_distance = distance_map.get(distance.upper(), models.Distance.COSINE)

    client.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=vector_size,
            distance=qdrant_distance
        )
    )

def get_vector_store(
    collection_name: str,
    embedding,
    client: Optional[QdrantClient] = None
) -> QdrantVectorStore:
    """
    Return a QdrantVectorStore for the specified collection and embedding model.
    """
    if client is None:
        client = get_client()
    return QdrantVectorStore(
        client=client,
        collection_name=collection_name,
        embedding=embedding
    )

def get_or_create_vector_store(
    collection_name: str,
    embedding,
    vector_size: int = 1536,
    distance: str = "COSINE",
    client: Optional[QdrantClient] = None
) -> QdrantVectorStore:
    """
    Get the vector store for a collection, creating the collection first if it doesn't exist.
    """
    if client is None:
        client = get_client()
        
    if not collection_exists(collection_name, client=client):
        create_collection(
            collection_name=collection_name,
            vector_size=vector_size,
            distance=distance,
            client=client
        )
        
    return get_vector_store(collection_name=collection_name, embedding=embedding, client=client)


def delete_document_vectors(
    collection_name: str,
    *,
    user_id: str,
    document_id: str,
    client: Optional[QdrantClient] = None,
) -> None:
    """Delete every vector belonging to one user's document."""
    if client is None:
        client = get_client()
    if not collection_exists(collection_name, client=client):
        return

    client.delete(
        collection_name=collection_name,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="metadata.user_id", match=models.MatchValue(value=user_id)
                    ),
                    models.FieldCondition(
                        key="metadata.doc_id",
                        match=models.MatchValue(value=document_id),
                    ),
                ]
            )
        ),
        wait=True,
    )
