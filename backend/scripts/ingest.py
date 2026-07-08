from dotenv import load_dotenv
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from utils.pydantic_schemas import IngestData
from scripts.intention_pipelines.summarization_pipeline.level1_pdf_with_outline import build_tree_from_pdf, find_node_id
from pathlib import Path
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

collection_name = os.getenv("QDRANT_COLLECTION_NAME")
if not collection_name:
    raise ValueError("QDRANT_COLLECTION_NAME is not set in the environment variables")


def ingest_pdf(data: IngestData):
    """
    Load a PDF, chunk it, create embeddings, and store the vectors in Qdrant.
    """

    """   
    secure_url: str = Field(...,description="Secure URL of the uploaded PDF")
    filename: str = Field(...,description="Filename of the uploaded PDF")
    doc_id: str = Field(...,description="Document ID")
    user_id: str = Field(...,description="User ID")
    """

    # embedding model 
    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-small",
        dimensions=1536
    )

    client = qdrant_manager.get_client()
    
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
            "doc_id": data.doc_id,
            "user_id": data.user_id,
            "node_id": find_node_id(page,nodes)
        })

    # text splitting(chunking)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(documents)

    print(f"Total chunks created: {len(chunks)}")

    # Store chunks in Qdrant using qdrant_manager
    print("Initializing Qdrant vector store and uploading chunks...")
    vector_store = qdrant_manager.get_or_create_vector_store(
        collection_name=collection_name,
        embedding=embeddings,
        vector_size=1536,
        client=client
    )
    vector_store.add_documents(chunks)
    print("Successfully stored chunks in Qdrant!")
    return nodes
