from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from scripts.data_analysis_agent.retrieval.hybrid_retrieval_subgraph import (
    hybrid_retrieval_subgraph,
)

from .nodes import AsyncRetrievalGraph, build_hydration_node, build_retrieval_node
from .repositories import EvidenceRepository, MongoEvidenceRepository
from .state import AnalysisPhase, DataAnalysisState


RETRIEVE_EVIDENCE_NODE = "retrieve_evidence"
HYDRATE_EVIDENCE_NODE = "hydrate_evidence"


def _after_retrieval(state: DataAnalysisState) -> Literal["hydrate", "end"]:
    return "end" if state["phase"] == AnalysisPhase.FAILED else "hydrate"


def build_data_analysis_graph(
    *,
    retrieval_graph: AsyncRetrievalGraph | None = None,
    evidence_repository: EvidenceRepository | None = None,
) -> Any:
    """Build the current parent workflow: retrieve, then hydrate evidence."""

    selected_retrieval_graph = retrieval_graph or hybrid_retrieval_subgraph
    selected_repository = evidence_repository or MongoEvidenceRepository()

    builder = StateGraph(DataAnalysisState)
    builder.add_node(
        RETRIEVE_EVIDENCE_NODE,
        build_retrieval_node(selected_retrieval_graph),
    )
    builder.add_node(
        HYDRATE_EVIDENCE_NODE,
        build_hydration_node(selected_repository),
    )
    builder.add_edge(START, RETRIEVE_EVIDENCE_NODE)
    builder.add_conditional_edges(
        RETRIEVE_EVIDENCE_NODE,
        _after_retrieval,
        {"hydrate": HYDRATE_EVIDENCE_NODE, "end": END},
    )
    builder.add_edge(HYDRATE_EVIDENCE_NODE, END)
    return builder.compile()


data_analysis_graph = build_data_analysis_graph()
