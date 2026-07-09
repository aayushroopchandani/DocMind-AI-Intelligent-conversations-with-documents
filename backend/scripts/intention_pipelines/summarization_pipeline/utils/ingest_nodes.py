import os
from typing import Literal

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings

from db import crud
from qdrant_manager import get_or_create_vector_store

load_dotenv()


NodeIngestionStatus = Literal["ready", "not_ready"]


async def get_nodes_ingestion_status(*, user_id: str, doc_id: str) -> NodeIngestionStatus:
    status = await crud.get_nodes_ingestion_status(
        user_id=user_id,
        document_id=doc_id,
    )
    if status is None:
        raise ValueError("Document not found")
    return status


async def ingest_nodes(
    nodes: list[dict],
    doc_id: str,
    user_id: str,
) -> NodeIngestionStatus:
    if await get_nodes_ingestion_status(user_id=user_id, doc_id=doc_id) == "ready":
        print("Nodes embeddings already exist in the vector db!")
        return "ready"

    embedding = OpenAIEmbeddings(
        model="text-embedding-3-small",
        dimensions=512,
    )

    vector_store = get_or_create_vector_store(
        collection_name=os.getenv("QDRANT_COLLECTION_NAME_NODES"),
        embedding=embedding,
        vector_size=512,
    )

    nodes_documents = [
        Document(
            page_content=node["title"],
            metadata={
                **{k: v for k, v in node.items() if k != "title"},
                "doc_id": doc_id,
                "user_id": user_id,
            },
        )
        for node in nodes
    ]

    if nodes_documents:
        vector_store.add_documents(nodes_documents)

    marked_ready = await crud.mark_nodes_ingestion_ready(
        user_id=user_id,
        document_id=doc_id,
    )
    if not marked_ready:
        raise ValueError("Document not found")

    print("Nodes embeddings added to the vector db!")
    return "ready"
