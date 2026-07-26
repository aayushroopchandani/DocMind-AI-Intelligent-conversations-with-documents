from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from scripts.data_analysis_agent.retrieval.hybrid_retrieval_subgraph import (
    hybrid_retrieval_subgraph,
)

from .nodes import (
    AsyncRetrievalGraph,
    build_assessment_node,
    build_completion_node,
    build_hydration_node,
    build_profiling_node,
    build_preparation_node,
    build_requirements_node,
    build_retrieval_node,
)
from .repositories import (
    AssessmentCache,
    AssessmentMetadataRepository,
    DatasetRepository,
    EvidenceRepository,
    MongoAssessmentCache,
    MongoAssessmentMetadataRepository,
    MongoDatasetRepository,
    MongoDerivedDatasetRepository,
    MongoEvidenceRepository,
    MongoProfileCache,
    MongoNormalizedDatasetRepository,
    MongoRequirementsCache,
    MongoTextExtractionCache,
    ProfileCache,
    NormalizedDatasetRepository,
    RequirementsCache,
)
from .services import (
    AmbiguityResolver,
    AnalysisRequirementsRunner,
    DatasetProfiler,
    DatasetProfilingRunner,
    DeterministicEvidenceMatcher,
    EvidenceAssessmentRunner,
    EvidenceCompletionRunner,
    DatasetPreparationRunner,
    QdrantTargetedRepairRetriever,
    RequirementsExtractor,
    TextEvidenceCompletionService,
)
from .models import EvidenceAssessment, ReadinessDecision
from .state import AnalysisPhase, DataAnalysisState


RETRIEVE_EVIDENCE_NODE = "retrieve_evidence"
HYDRATE_EVIDENCE_NODE = "hydrate_evidence"
PROFILE_DATASETS_NODE = "profile_datasets"
EXTRACT_REQUIREMENTS_NODE = "extract_requirements"
ASSESS_EVIDENCE_NODE = "assess_evidence"
COMPLETE_EVIDENCE_NODE = "complete_evidence"
PREPARE_DATASETS_NODE = "prepare_datasets"
ASSESSMENT_GATE_NODE = "assessment_gate"


def _after_retrieval(state: DataAnalysisState) -> Literal["hydrate", "end"]:
    return "end" if state["phase"] == AnalysisPhase.FAILED else "hydrate"


def _after_hydration(state: DataAnalysisState) -> Literal["profile", "end"]:
    return "end" if state["phase"] == AnalysisPhase.FAILED else "profile"


def _after_profiling(state: DataAnalysisState) -> Literal["assess", "end"]:
    return "end" if state["phase"] == AnalysisPhase.FAILED else "assess"


def _assessment_gate(_state: DataAnalysisState) -> dict[str, Any]:
    return {}


def _after_assessment(
    state: DataAnalysisState,
) -> Literal["prepare", "complete", "end"]:
    if state["phase"] == AnalysisPhase.FAILED:
        return "end"
    assessment = EvidenceAssessment.model_validate(state["evidence_assessment"])
    if assessment.decision == ReadinessDecision.READY:
        return "prepare"
    if assessment.decision in {
        ReadinessDecision.NEEDS_CLARIFICATION,
        ReadinessDecision.UNANSWERABLE,
    }:
        return "end"
    return "complete"


def _after_completion(
    state: DataAnalysisState,
) -> Literal["prepare", "end"]:
    if state["phase"] == AnalysisPhase.FAILED:
        return "end"
    assessment = EvidenceAssessment.model_validate(state["evidence_assessment"])
    return (
        "prepare"
        if assessment.decision == ReadinessDecision.READY
        else "end"
    )


def build_data_analysis_graph(
    *,
    retrieval_graph: AsyncRetrievalGraph | None = None,
    evidence_repository: EvidenceRepository | None = None,
    dataset_repository: DatasetRepository | None = None,
    profile_cache: ProfileCache | None = None,
    normalized_dataset_repository: NormalizedDatasetRepository | None = None,
    dataset_profiler: DatasetProfiler | None = None,
    profile_concurrency: int | None = None,
    requirements_cache: RequirementsCache | None = None,
    requirements_extractor: RequirementsExtractor | None = None,
    assessment_metadata_repository: AssessmentMetadataRepository | None = None,
    assessment_cache: AssessmentCache | None = None,
    evidence_matcher: DeterministicEvidenceMatcher | None = None,
    ambiguity_resolver: AmbiguityResolver | None = None,
    completion_runner: EvidenceCompletionRunner | None = None,
    preparation_runner: DatasetPreparationRunner | None = None,
    preparation_concurrency: int | None = None,
) -> Any:
    """Build the graph through final-ready dataset preparation."""

    selected_retrieval_graph = retrieval_graph or hybrid_retrieval_subgraph
    selected_repository = evidence_repository or MongoEvidenceRepository()
    selected_dataset_repository = dataset_repository or MongoDatasetRepository()
    profiling_runner = DatasetProfilingRunner(
        dataset_repository=selected_dataset_repository,
        profile_cache=profile_cache or MongoProfileCache(),
        profiler=dataset_profiler,
        max_concurrency=profile_concurrency,
    )
    requirements_runner = AnalysisRequirementsRunner(
        cache=requirements_cache or MongoRequirementsCache(),
        extractor=requirements_extractor,
    )
    assessment_runner = EvidenceAssessmentRunner(
        metadata_repository=(
            assessment_metadata_repository
            or MongoAssessmentMetadataRepository()
        ),
        cache=assessment_cache or MongoAssessmentCache(),
        matcher=evidence_matcher,
        resolver=ambiguity_resolver,
    )
    selected_completion_runner = completion_runner or EvidenceCompletionRunner(
        evidence_repository=selected_repository,
        profiling_runner=profiling_runner,
        assessment_runner=assessment_runner,
        text_service=TextEvidenceCompletionService(
            cache=MongoTextExtractionCache(),
            derived_repository=MongoDerivedDatasetRepository(),
        ),
        repair_retriever=QdrantTargetedRepairRetriever(),
    )
    selected_preparation_runner = preparation_runner or DatasetPreparationRunner(
        dataset_repository=selected_dataset_repository,
        normalized_repository=(
            normalized_dataset_repository or MongoNormalizedDatasetRepository()
        ),
        max_concurrency=preparation_concurrency,
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
    builder.add_node(
        EXTRACT_REQUIREMENTS_NODE,
        build_requirements_node(requirements_runner),
    )
    builder.add_node(
        ASSESS_EVIDENCE_NODE,
        build_assessment_node(assessment_runner),
    )
    builder.add_node(
        COMPLETE_EVIDENCE_NODE,
        build_completion_node(selected_completion_runner),
    )
    builder.add_node(
        PREPARE_DATASETS_NODE,
        build_preparation_node(selected_preparation_runner),
    )
    builder.add_node(ASSESSMENT_GATE_NODE, _assessment_gate)
    builder.add_edge(START, RETRIEVE_EVIDENCE_NODE)
    builder.add_edge(START, EXTRACT_REQUIREMENTS_NODE)
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
    builder.add_edge(
        [PROFILE_DATASETS_NODE, EXTRACT_REQUIREMENTS_NODE],
        ASSESSMENT_GATE_NODE,
    )
    builder.add_conditional_edges(
        ASSESSMENT_GATE_NODE,
        _after_profiling,
        {"assess": ASSESS_EVIDENCE_NODE, "end": END},
    )
    builder.add_conditional_edges(
        ASSESS_EVIDENCE_NODE,
        _after_assessment,
        {
            "prepare": PREPARE_DATASETS_NODE,
            "complete": COMPLETE_EVIDENCE_NODE,
            "end": END,
        },
    )
    builder.add_conditional_edges(
        COMPLETE_EVIDENCE_NODE,
        _after_completion,
        {"prepare": PREPARE_DATASETS_NODE, "end": END},
    )
    builder.add_edge(PREPARE_DATASETS_NODE, END)
    return builder.compile()


data_analysis_graph = build_data_analysis_graph()
