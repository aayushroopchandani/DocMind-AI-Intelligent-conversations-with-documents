from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .fusion import ResultSelector, build_fusion_node
from .query_generation import AsyncQueryGenerator, build_query_generation_node
from .query_generation_subgraph import QUERY_GENERATION_NODE
from .state import DataAnalysisRetrievalState
from .table_retrieval import AsyncTableRetriever, build_table_retrieval_node
from .text_retrieval import AsyncTextRetriever, build_text_retrieval_node


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

    builder = StateGraph(DataAnalysisRetrievalState)
    builder.add_node(
        QUERY_GENERATION_NODE,
        build_query_generation_node(query_generator),
    )
    builder.add_node(TEXT_RETRIEVAL_NODE, build_text_retrieval_node(text_retriever))
    builder.add_node(
        TABLE_RETRIEVAL_NODE,
        build_table_retrieval_node(table_retriever),
    )
    builder.add_node(FUSION_NODE, build_fusion_node(result_selector))
    builder.add_edge(START, QUERY_GENERATION_NODE)
    builder.add_edge(QUERY_GENERATION_NODE, TEXT_RETRIEVAL_NODE)
    builder.add_edge(QUERY_GENERATION_NODE, TABLE_RETRIEVAL_NODE)
    builder.add_edge([TEXT_RETRIEVAL_NODE, TABLE_RETRIEVAL_NODE], FUSION_NODE)
    builder.add_edge(FUSION_NODE, END)
    return builder.compile()


hybrid_retrieval_subgraph = build_hybrid_retrieval_subgraph()
