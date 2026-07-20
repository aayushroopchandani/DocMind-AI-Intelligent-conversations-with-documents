from dotenv import load_dotenv
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import models
from utils.pydantic_schemas import IngestData
from utils.embeddings import get_chunk_embedding
from scripts.intention_pipelines.summarization_pipeline.utils.getting_outline_for_l1 import build_tree_from_pdf, find_node_id
import tempfile
import requests
import sys
import os
from uuid import NAMESPACE_URL, uuid5


# Add parent directory of scripts to sys.path to allow importing backend modules
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

import qdrant_manager
from scripts.data_analysis_agent.reterival.utils.sparse_index import (
    SparseRecord,
    delete_sparse_by_filter,
    delete_sparse_ids,
    text_sparse_collection_name,
    upsert_sparse_records,
)

load_dotenv()

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100
DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


def _chunk_collection_name() -> str:
    collection_name = os.getenv("QDRANT_COLLECTION_NAME")
    if not collection_name:
        raise RuntimeError("QDRANT_COLLECTION_NAME is not configured")
    return collection_name


def _add_chunk_order_metadata(chunks: list) -> None:
    """Add stable ordering metadata before chunks are stored in Qdrant."""
    node_chunk_counts: dict[str, int] = {}

    for document_chunk_index, chunk in enumerate(chunks):
        metadata = chunk.metadata
        node_id = metadata.get("node_id")
        node_key = str(node_id) if node_id is not None else "__missing_node__"
        chunk_index = node_chunk_counts.get(node_key, 0)

        metadata["chunk_index"] = chunk_index
        metadata["document_chunk_index"] = document_chunk_index

        if "page_number" not in metadata:
            page = metadata.get("page")
            if isinstance(page, int):
                metadata["page_number"] = page + 1

        node_chunk_counts[node_key] = chunk_index + 1


def _stable_chunk_ids(chunks: list, *, user_id: str, document_id: str) -> list[str]:
    return [
        str(
            uuid5(
                NAMESPACE_URL,
                "|".join(
                    (
                        "docmind-chunk",
                        user_id,
                        document_id,
                        str(chunk.metadata.get("document_chunk_index", index)),
                        str(chunk.metadata.get("page_number", "")),
                        str(chunk.metadata.get("start_index", "")),
                    )
                ),
            )
        )
        for index, chunk in enumerate(chunks)
    ]


def ingest_pdf_path(
    pdf_path: str,
    data: IngestData,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    separators: list[str] | None = None,
    replace_existing: bool = False,
):
    """Ingest a PDF that is already available on the local filesystem."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be between zero and chunk_size")

    loader = PyMuPDFLoader(pdf_path)
    documents = loader.load()
    nodes = build_tree_from_pdf(pdf_path)

    for document in documents:
        page = document.metadata.get("page", 0) + 1  # 0-based to 1-based
        node_id = find_node_id(page, nodes)
        # Outlined PDFs often have cover/front-matter pages before the first
        # TOC entry. Keep those chunks indexable instead of leaving them outside
        # every per-node representative set.
        if node_id is None and nodes:
            node_id = nodes[0]["node_id"]
        document.metadata.update(
            {
                "source": data.filename,
                "doc_id": data.document_id,
                "user_id": data.user_id,
                "page_number": page,
                "node_id": node_id,
            }
        )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=separators or DEFAULT_SEPARATORS,
        add_start_index=True,
    )
    chunks = splitter.split_documents(documents)
    _add_chunk_order_metadata(chunks)

    print(f"Total chunks created: {len(chunks)}")
    print("Initializing Qdrant vector store and uploading chunks...")
    vector_store = qdrant_manager.get_chunk_vector_store(
        embedding=get_chunk_embedding(),
    )
    chunk_ids = _stable_chunk_ids(
        chunks, user_id=data.user_id, document_id=data.document_id
    )
    old_point_ids = (
        qdrant_manager.get_document_vector_ids(
            collection_name=_chunk_collection_name(),
            user_id=data.user_id,
            document_id=data.document_id,
        )
        if replace_existing
        else []
    )
    # LangChain embeds before upserting. Existing points therefore remain
    # available if the embedding call fails during a replacement ingestion.
    vector_store.add_documents(chunks, ids=chunk_ids)
    sparse_collection = text_sparse_collection_name(_chunk_collection_name())
    upsert_sparse_records(
        sparse_collection,
        [
            SparseRecord(
                point_id=point_id,
                text=chunk.page_content,
                payload={
                    "page_content": chunk.page_content,
                    "metadata": dict(chunk.metadata),
                },
            )
            for point_id, chunk in zip(chunk_ids, chunks, strict=True)
        ],
        payload_indexes=qdrant_manager.DEFAULT_PAYLOAD_INDEXES,
    )
    if old_point_ids:
        current_ids = set(chunk_ids)
        obsolete_ids = [
            point_id for point_id in old_point_ids if str(point_id) not in current_ids
        ]
        qdrant_manager.delete_vector_ids(
            collection_name=_chunk_collection_name(),
            point_ids=obsolete_ids,
        )
        delete_sparse_ids(sparse_collection, obsolete_ids)
    print("Successfully stored chunks in Qdrant!")
    return nodes


def ingest_pdf(
    data: IngestData,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    separators: list[str] | None = None,
):
    """
    Load a PDF, chunk it, create embeddings, and store the vectors in Qdrant.
    """

    """   
    secure_url: str = Field(...,description="Secure URL of the uploaded PDF")
    filename: str = Field(...,description="Filename of the uploaded PDF")
    document_id: str = Field(..., description="SHA-256 hash of the PDF content")
    user_id: str = Field(...,description="User ID")
    """

    # download the pdf from the secure url

    try:
        response = requests.get(data.secure_url)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"Request failed: {e}")
        raise ValueError(f"Failed to download PDF from {data.secure_url}")
    
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp_file: 
        for chunk in response.iter_content(8192):
            temp_file.write(chunk)

        temp_path = temp_file.name

    print(f"Ingesting PDF: {data.filename} and generating embeddings...")
    try:
        return ingest_pdf_path(
            temp_path,
            data,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=separators,
        )
    finally:
        os.remove(temp_path)


def delete_pdf_embeddings(*, user_id: str, document_id: str) -> None:
    """Remove all Qdrant chunks for a document that no chat references."""
    qdrant_manager.delete_document_vectors(
        collection_name=_chunk_collection_name(),
        user_id=user_id,
        document_id=document_id,
    )
    delete_sparse_by_filter(
        text_sparse_collection_name(_chunk_collection_name()),
        models.Filter(
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
        ),
    )
