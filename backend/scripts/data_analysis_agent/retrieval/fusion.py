from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .state import DataAnalysisRetrievalState
from .utils.diversity import select_tables, select_text_chunks
from .utils.limits import limits_for_scope
from .utils.query_sets import combine_queries
from .utils.relevance import (
    RetrievalSignals,
    build_scoring_context,
    score_table_candidate,
    score_text_candidate,
    table_candidate_content,
    text_candidate_content,
)
from .utils.selection_policy import selection_policy


class ResultSelector(Protocol):
    def select(self, state: DataAnalysisRetrievalState) -> Mapping[str, Any]: ...


class DeterministicResultSelector:
    """Score, deduplicate, diversify, and trim hybrid retrieval candidates."""

    def select(self, state: DataAnalysisRetrievalState) -> dict[str, Any]:
        scope = state.get("retrieval_scope", "normal")
        table_intent = state.get("table_intent", "supporting")
        limits = limits_for_scope(scope)
        policy = selection_policy(scope=scope, table_intent=table_intent)
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
        text_candidates = state.get("retrieved_text_chunks", [])
        text_context = build_scoring_context(
            text_candidates,
            signals=signals,
            query_count=text_query_count,
            candidate_text=text_candidate_content,
        )
        scored_text = [
            score_text_candidate(
                candidate,
                context=text_context,
            )
            for candidate in text_candidates
        ]
        scored_tables: list[dict[str, Any]] = []
        if table_intent != "none":
            table_candidates = state.get("retrieved_tables", [])
            table_context = build_scoring_context(
                table_candidates,
                signals=signals,
                query_count=table_query_count,
                candidate_text=table_candidate_content,
            )
            scored_tables = [
                score_table_candidate(candidate, context=table_context)
                for candidate in table_candidates
            ]
            scored_tables = [
                candidate
                for candidate in scored_tables
                if max(
                    candidate["relevance_features"]["concept"],
                    candidate["relevance_features"]["schema"],
                )
                >= policy.table_minimum_substantive_match
                or (
                    not signals.concepts
                    and candidate["relevance_features"]["rrf"] >= 0.90
                )
            ]
        broad = scope == "broad"
        return {
            "final_text_chunks": select_text_chunks(
                scored_text,
                limit=limits.final_text_chunks,
                broad=broad,
                minimum_score=policy.text_minimum_score,
                minimum_ratio=policy.text_relative_ratio,
            ),
            "final_tables": select_tables(
                scored_tables,
                limit=limits.final_tables,
                broad=broad,
                minimum_score=policy.table_minimum_score,
                minimum_ratio=policy.table_relative_ratio,
            ),
        }


def build_fusion_node(selector: ResultSelector | None = None) -> Any:
    selected = selector or DeterministicResultSelector()

    def fuse_results(state: DataAnalysisRetrievalState) -> Mapping[str, Any]:
        return selected.select(state)

    return fuse_results
