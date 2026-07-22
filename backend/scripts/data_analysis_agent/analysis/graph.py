from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from scripts.data_analysis_agent.retrieval.hybrid_retrieval_subgraph import (
    hybrid_retrieval_subgraph,
)

from .nodes import (
    AsyncRetrievalGraph,
    build_hydration_node,
    build_profiling_node,
    build_retrieval_node,
)
from .repositories import (
    DatasetRepository,
    EvidenceRepository,
    MongoDatasetRepository,
    MongoEvidenceRepository,
    MongoProfileCache,
    ProfileCache,
)
from .services import DatasetProfiler, DatasetProfilingRunner
from .state import AnalysisPhase, DataAnalysisState


RETRIEVE_EVIDENCE_NODE = "retrieve_evidence"
HYDRATE_EVIDENCE_NODE = "hydrate_evidence"
PROFILE_DATASETS_NODE = "profile_datasets"


def _after_retrieval(state: DataAnalysisState) -> Literal["hydrate", "end"]:
    return "end" if state["phase"] == AnalysisPhase.FAILED else "hydrate"


def _after_hydration(state: DataAnalysisState) -> Literal["profile", "end"]:
    return "end" if state["phase"] == AnalysisPhase.FAILED else "profile"


def build_data_analysis_graph(
    *,
    retrieval_graph: AsyncRetrievalGraph | None = None,
    evidence_repository: EvidenceRepository | None = None,
    dataset_repository: DatasetRepository | None = None,
    profile_cache: ProfileCache | None = None,
    dataset_profiler: DatasetProfiler | None = None,
    profile_concurrency: int | None = None,
) -> Any:
    """Build the current parent workflow: retrieve, hydrate, then profile."""

    selected_retrieval_graph = retrieval_graph or hybrid_retrieval_subgraph
    selected_repository = evidence_repository or MongoEvidenceRepository()
    profiling_runner = DatasetProfilingRunner(
        dataset_repository=dataset_repository or MongoDatasetRepository(),
        profile_cache=profile_cache or MongoProfileCache(),
        profiler=dataset_profiler,
        max_concurrency=profile_concurrency,
    )

    builder = StateGraph(DataAnalysisState)
    builder.add_node(
        RETRIEVE_EVIDENCE_NODE,
        build_retrieval_node(selected_retrieval_graph),
    )
    builder.add_node(
        HYDRATE_EVIDENCE_NODE,
        build_hydration_node(selected_repository),
    )
    builder.add_node(
        PROFILE_DATASETS_NODE,
        build_profiling_node(profiling_runner),
    )
    builder.add_edge(START, RETRIEVE_EVIDENCE_NODE)
    builder.add_conditional_edges(
        RETRIEVE_EVIDENCE_NODE,
        _after_retrieval,
        {"hydrate": HYDRATE_EVIDENCE_NODE, "end": END},
    )
    builder.add_conditional_edges(
        HYDRATE_EVIDENCE_NODE,
        _after_hydration,
        {"profile": PROFILE_DATASETS_NODE, "end": END},
    )
    builder.add_edge(PROFILE_DATASETS_NODE, END)
    return builder.compile()


data_analysis_graph = build_data_analysis_graph()
