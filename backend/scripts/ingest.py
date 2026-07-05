from dotenv import load_dotenv
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from pathlib import Path
import sys
import os

# Add parent directory of scripts to sys.path to allow importing backend modules
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

import qdrant_manager

load_dotenv()

def ingest_pdf(pdf_path: str, collection_name: str = "doc_mind_chat_pdf"):
    """
    Load a PDF, chunk it, create embeddings, and store the vectors in Qdrant.
    """
    # embedding model 
    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-small",
        dimensions=1536
    )

    client = qdrant_manager.get_client()

    # Check if collection exists and has vectors
    exists = qdrant_manager.collection_exists(collection_name=collection_name, client=client)
    
    if exists:
        try:
            info = client.get_collection(collection_name)
            if info.points_count > 0:
                print(f"Collection '{collection_name}' already exists with {info.points_count} vectors. Skipping ingestion.")
                return
        except Exception as e:
            print(f"Error checking collection, will proceed: {e}")

    print(f"Ingesting PDF: {pdf_path} and generating embeddings...")
    # document loader
    loader = PyMuPDFLoader(pdf_path)
    documents = loader.load()

    # text splitting(chunking)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(documents)

    for index, chunk in enumerate(chunks):
        chunk.metadata["chunk_id"] = f"chunk_{index}"

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

if __name__ == "__main__":
    # Locate sample PDF relative to the backend workspace directory
    base_dir = Path(__file__).resolve().parent.parent
    default_pdf = base_dir / "sample_pdfs" / "Building Machine Learning Systems with Python - Second Edition.pdf"
    
    if not default_pdf.exists():
        print(f"PDF file not found at {default_pdf}")
    else:
        ingest_pdf(str(default_pdf))
