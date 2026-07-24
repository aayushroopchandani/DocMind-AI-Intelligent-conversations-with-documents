from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .fusion import ResultSelector, build_fusion_node
from .query_generation import AsyncQueryGenerator, build_query_generation_node
from .query_generation_subgraph import QUERY_GENERATION_NODE
from .state import DataAnalysisRetrievalState
from .table_retrieval import AsyncTableRetriever, build_table_retrieval_node
from .table_retrieval import QdrantTableRetriever
from .text_retrieval import (
    AsyncTextRetriever,
    QdrantTextRetriever,
    build_text_retrieval_node,
)
from .utils.embedding_cache import SingleFlightEmbeddingCache
from .utils.hybrid_search import HybridQdrantSearcher


TEXT_RETRIEVAL_NODE = "retrieve_text"
TABLE_RETRIEVAL_NODE = "retrieve_tables"
FUSION_NODE = "fusion"


def build_hybrid_retrieval_subgraph(
    *,
    query_generator: AsyncQueryGenerator | None = None,
    text_retriever: AsyncTextRetriever | None = None,
    table_retriever: AsyncTableRetriever | None = None,
    result_selector: ResultSelector | None = None,
) -> Any:
    """Retrieve both evidence types, then select the final context."""

    selected_text_retriever = text_retriever
    selected_table_retriever = table_retriever
    if text_retriever is None and table_retriever is None:
        searcher = HybridQdrantSearcher(
            embeddings=SingleFlightEmbeddingCache()
        )
        selected_text_retriever = QdrantTextRetriever(searcher)
        selected_table_retriever = QdrantTableRetriever(searcher)

    builder = StateGraph(DataAnalysisRetrievalState)
    builder.add_node(
        QUERY_GENERATION_NODE,
        build_query_generation_node(query_generator),
    )
    builder.add_node(
        TEXT_RETRIEVAL_NODE,
        build_text_retrieval_node(selected_text_retriever),
    )
    builder.add_node(
        TABLE_RETRIEVAL_NODE,
        build_table_retrieval_node(selected_table_retriever),
    )
    builder.add_node(FUSION_NODE, build_fusion_node(result_selector))
    builder.add_edge(START, QUERY_GENERATION_NODE)
    builder.add_edge(QUERY_GENERATION_NODE, TEXT_RETRIEVAL_NODE)
    builder.add_edge(QUERY_GENERATION_NODE, TABLE_RETRIEVAL_NODE)
    builder.add_edge([TEXT_RETRIEVAL_NODE, TABLE_RETRIEVAL_NODE], FUSION_NODE)
    builder.add_edge(FUSION_NODE, END)
    return builder.compile()


hybrid_retrieval_subgraph = build_hybrid_retrieval_subgraph()
