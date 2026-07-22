from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig

from scripts.data_analysis_agent.retrieval.state import create_retrieval_state

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

        return {
            "phase": AnalysisPhase.RETRIEVED,
            "retrieval_result": retrieval_result,
        }

    return retrieve
