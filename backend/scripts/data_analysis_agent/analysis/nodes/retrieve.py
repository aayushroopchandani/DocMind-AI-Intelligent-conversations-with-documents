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
    RetrievalResult,
)
from ..state import AnalysisPhase, DataAnalysisState


logger = logging.getLogger(__name__)


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
        child_state = create_retrieval_state(
            user_id=request.user_id,
            chat_id=request.chat_id,
            query=request.query,
            document_ids=list(request.document_ids),
        )
        try:
            child_result = await retrieval_graph.ainvoke(child_state, config=config)
            retrieval_result = RetrievalResult.from_retrieval_state(child_result)
        except Exception:
            logger.exception("Data-analysis retrieval failed for run %s", state["run_id"])
            record_analysis_trace(
                metrics={
                    "retrieval_succeeded": False,
                    "analysis_failed_stage": IssueStage.RETRIEVAL,
                },
                tags=("retrieval:error",),
            )
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
            },
            tags=(
                f"retrieval-scope:{retrieval_result.retrieval_scope}",
                (
                    "query-generation:fallback"
                    if retrieval_result.diagnostics.query_generation_fallback
                    else "query-generation:llm"
                ),
            ),
        )
        return {
            "phase": AnalysisPhase.RETRIEVED,
            "retrieval_result": retrieval_result,
        }

    return retrieve
