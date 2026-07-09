from dotenv import load_dotenv
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from utils.pydantic_schemas import IngestData
from utils.embeddings import get_chunk_embedding
from scripts.intention_pipelines.summarization_pipeline.level1_pdf_with_outline import build_tree_from_pdf, find_node_id
import tempfile
import requests
import sys
import os


# Add parent directory of scripts to sys.path to allow importing backend modules
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

import qdrant_manager

load_dotenv()


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


def ingest_pdf(data: IngestData):
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
        loader = PyMuPDFLoader(temp_path)
        documents = loader.load()
        nodes = build_tree_from_pdf(temp_path)

    finally:
        os.remove(temp_path)    

    
    for document in documents:
        page = document.metadata.get("page", 0) + 1 # 0-based index to 1-based index
        document.metadata.update({
            "source": data.filename,
            "doc_id": data.document_id,
            "user_id": data.user_id,
            "page_number": page,
            "node_id": find_node_id(page,nodes)
        })

    # text splitting(chunking)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100,
        separators=["\n\n", "\n", ". ", " ", ""],
        add_start_index=True,
    )
    chunks = splitter.split_documents(documents)
    _add_chunk_order_metadata(chunks)

    print(f"Total chunks created: {len(chunks)}")

    # Store chunks in Qdrant using qdrant_manager
    print("Initializing Qdrant vector store and uploading chunks...")
    vector_store = qdrant_manager.get_chunk_vector_store(
        embedding=get_chunk_embedding(),
    )
    vector_store.add_documents(chunks)
    print("Successfully stored chunks in Qdrant!")
    return nodes


def delete_pdf_embeddings(*, user_id: str, document_id: str) -> None:
    """Remove all Qdrant chunks for a document that no chat references."""
    qdrant_manager.delete_document_vectors(
        collection_name=_chunk_collection_name(),
        user_id=user_id,
        document_id=document_id,
    )
