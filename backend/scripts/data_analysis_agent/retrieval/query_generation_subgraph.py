from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .query_generation import AsyncQueryGenerator, build_query_generation_node
from .state import DataAnalysisRetrievalState


QUERY_GENERATION_NODE = "generate_retrieval_queries"


def build_query_generation_subgraph(
    *,
    query_generator: AsyncQueryGenerator | None = None,
) -> Any:
    """Compile the reusable first-stage retrieval subgraph."""

    builder = StateGraph(DataAnalysisRetrievalState)
    builder.add_node(
        QUERY_GENERATION_NODE,
        build_query_generation_node(query_generator),
    )
    builder.add_edge(START, QUERY_GENERATION_NODE)
    builder.add_edge(QUERY_GENERATION_NODE, END)
    return builder.compile()


# The future parent graph supplies the durable MongoDB checkpointer. Compiling
# this child without its own checkpointer lets it inherit the parent's thread.
query_generation_subgraph = build_query_generation_subgraph()
