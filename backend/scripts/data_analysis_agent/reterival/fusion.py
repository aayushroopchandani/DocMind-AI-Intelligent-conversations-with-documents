from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .state import DataAnalysisRetrievalState
from .utils.diversity import select_tables, select_text_chunks
from .utils.limits import limits_for_scope
from .utils.query_sets import combine_queries
from .utils.relevance import (
    RetrievalSignals,
    score_table_candidate,
    score_text_candidate,
)


class ResultSelector(Protocol):
    def select(self, state: DataAnalysisRetrievalState) -> Mapping[str, Any]: ...


class DeterministicResultSelector:
    """Score, deduplicate, diversify, and trim hybrid retrieval candidates."""

    def select(self, state: DataAnalysisRetrievalState) -> dict[str, Any]:
        scope = state.get("retrieval_scope", "normal")
        limits = limits_for_scope(scope)
        signals = RetrievalSignals.from_state(state)
        text_query_count = len(
            combine_queries(
                state["query"],
                state.get("shared_queries", []),
                state.get("text_queries", []),
            )
        )
        table_query_count = len(
            combine_queries(
                state["query"],
                state.get("shared_queries", []),
                state.get("table_queries", []),
            )
        )
        scored_text = [
            score_text_candidate(
                candidate,
                signals=signals,
                query_count=text_query_count,
            )
            for candidate in state.get("retrieved_text_chunks", [])
        ]
        scored_tables = [
            score_table_candidate(
                candidate,
                signals=signals,
                query_count=table_query_count,
            )
            for candidate in state.get("retrieved_tables", [])
        ]
        broad = scope == "broad"
        return {
            "final_text_chunks": select_text_chunks(
                scored_text,
                limit=limits.final_text_chunks,
                broad=broad,
            ),
            "final_tables": select_tables(
                scored_tables,
                limit=limits.final_tables,
                broad=broad,
            ),
        }


def build_fusion_node(selector: ResultSelector | None = None) -> Any:
    selected = selector or DeterministicResultSelector()

    def fuse_results(state: DataAnalysisRetrievalState) -> Mapping[str, Any]:
        return selected.select(state)

    return fuse_results
