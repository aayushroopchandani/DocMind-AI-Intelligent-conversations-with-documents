from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig

from scripts.data_analysis_agent.retrieval.state import create_retrieval_state

from ..observability import record_analysis_trace
from ..models import (
    AnalysisIssue,
    AnalysisRequest,
    IssueCode,
    IssueSeverity,
    IssueStage,
    RetrievedTableReference,
    RetrievalResult,
    RetrievalSignals,
)
from ..state import AnalysisPhase, DataAnalysisState


logger = logging.getLogger(__name__)


def _pinned_references(
    request: AnalysisRequest,
) -> tuple[RetrievedTableReference, ...]:
    return tuple(
        RetrievedTableReference(
            table_id=dataset.dataset_id,
            document_id=dataset.source_container_id,
            source_type=dataset.source_type.value,
            source_version=dataset.source_version,
            title=dataset.title,
            page_start=getattr(dataset.locator, "page_start", None),
            page_end=getattr(dataset.locator, "page_end", None),
            expected_columns=tuple(column.key for column in dataset.columns),
            expected_units=tuple(
                dict.fromkeys(
                    column.unit for column in dataset.columns if column.unit
                )
            ),
            relevance_score=1.0,
            matched_queries=(request.query,),
            retrieval_modes=("pinned",),
        )
        for dataset in request.pinned_datasets
    )


def _merge_pinned(
    result: RetrievalResult,
    pinned: tuple[RetrievedTableReference, ...],
) -> RetrievalResult:
    if not pinned:
        return result
    existing = {item.table_id for item in result.table_references}
    return result.model_copy(
        update={
            "table_intent": "required",
            "table_references": (
                *result.table_references,
                *(item for item in pinned if item.table_id not in existing),
            ),
        }
    )


class AsyncRetrievalGraph(Protocol):
    async def ainvoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Mapping[str, Any]: ...


def build_retrieval_node(retrieval_graph: AsyncRetrievalGraph) -> Any:
    """Adapt the retrieval child graph into one lean parent artifact."""

    async def retrieve(
        state: DataAnalysisState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        request = AnalysisRequest.model_validate(state["request"])
        pinned = _pinned_references(request)
        if not request.document_ids:
            return {
                "phase": AnalysisPhase.RETRIEVED,
                "retrieval_result": RetrievalResult(
                    retrieval_scope="normal",
                    table_intent="required",
                    signals=RetrievalSignals(),
                    table_references=pinned,
                ),
            }
        child_state = create_retrieval_state(
            user_id=request.user_id,
            chat_id=request.chat_id,
            query=request.query,
            document_ids=list(request.document_ids),
        )
        try:
            child_result = await retrieval_graph.ainvoke(child_state, config=config)
            retrieval_result = _merge_pinned(
                RetrievalResult.from_retrieval_state(child_result),
                pinned,
            )
        except Exception:
            logger.exception("Data-analysis retrieval failed for run %s", state["run_id"])
            record_analysis_trace(
                metrics={
                    "retrieval_succeeded": False,
                    "analysis_failed_stage": IssueStage.RETRIEVAL,
                },
                tags=("retrieval:error",),
            )
            if pinned:
                return {
                    "phase": AnalysisPhase.RETRIEVED,
                    "retrieval_result": RetrievalResult(
                        retrieval_scope="normal",
                        table_intent="required",
                        signals=RetrievalSignals(),
                        table_references=pinned,
                    ),
                    "warnings": [
                        AnalysisIssue(
                            code=IssueCode.RETRIEVAL_FAILED,
                            severity=IssueSeverity.WARNING,
                            stage=IssueStage.RETRIEVAL,
                            message=(
                                "PDF evidence retrieval failed; pinned datasets "
                                "remain available."
                            ),
                            retryable=True,
                        )
                    ],
                }
            return {
                "phase": AnalysisPhase.FAILED,
                "errors": [
                    AnalysisIssue(
                        code=IssueCode.RETRIEVAL_FAILED,
                        severity=IssueSeverity.ERROR,
                        stage=IssueStage.RETRIEVAL,
                        message="Relevant source evidence could not be retrieved.",
                        retryable=True,
                    )
                ],
            }

        record_analysis_trace(
            metrics={
                "retrieval_succeeded": True,
                "retrieval_scope": retrieval_result.retrieval_scope,
                "retrieval_table_intent": retrieval_result.table_intent,
                "retrieved_text_chunk_count": len(
                    retrieval_result.text_evidence
                ),
                "retrieved_table_reference_count": len(
                    retrieval_result.table_references
                ),
                "query_generation_attempts": (
                    retrieval_result.diagnostics.query_generation_attempts
                ),
                "query_generation_fallback": (
                    retrieval_result.diagnostics.query_generation_fallback
                ),
                "query_generation_cache_hit": (
                    retrieval_result.diagnostics.query_generation_cache_hit
                ),
            },
            tags=(
                f"retrieval-scope:{retrieval_result.retrieval_scope}",
                (
                    "query-generation:fallback"
                    if retrieval_result.diagnostics.query_generation_fallback
                    else "query-generation:cache"
                    if retrieval_result.diagnostics.query_generation_cache_hit
                    else "query-generation:llm"
                ),
            ),
        )
        return {
            "phase": AnalysisPhase.RETRIEVED,
            "retrieval_result": retrieval_result,
        }

    return retrieve
