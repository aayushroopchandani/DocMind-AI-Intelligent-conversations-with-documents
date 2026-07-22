from __future__ import annotations

import unittest
from typing import Any

from pydantic import ValidationError

from scripts.data_analysis_agent.reterival.query_generation import (
    ConceptKind,
    GeneratedRetrievalQueries,
    MatchConcept,
    RetrievalScope,
    TableIntent,
)
from scripts.data_analysis_agent.reterival.query_generation_subgraph import (
    build_query_generation_subgraph,
)
from scripts.data_analysis_agent.reterival.state import (
    create_retrieval_state,
    retrieval_thread_config,
)


class _FakeQueryGenerator:
    def __init__(self) -> None:
        self.calls = 0
        self.input: Any = None

    async def ainvoke(self, input: Any, **kwargs: Any) -> GeneratedRetrievalQueries:
        self.calls += 1
        self.input = input
        return GeneratedRetrievalQueries(
            retrieval_scope=RetrievalScope.NORMAL,
            shared_queries=[
                "net income across available years",
                "annual profitability comparison",
            ],
            text_queries=[
                "how company profitability changed across available years",
                "discussion of profit after tax trends",
            ],
            table_queries=[
                "net income by year table",
                "financial statement profit after tax columns",
            ],
            table_intent=TableIntent.REQUIRED,
            match_concepts=[
                MatchConcept(
                    canonical="net income",
                    variants=["profit after tax"],
                    kind=ConceptKind.METRIC,
                )
            ],
            metrics=["net income", "profit after tax"],
            years=[],
            entities=[],
            units=[],
            column_terms=["year", "net income"],
        )


class QueryGenerationSubgraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_subgraph_generates_all_query_types_with_one_llm_call(self) -> None:
        generator = _FakeQueryGenerator()
        graph = build_query_generation_subgraph(query_generator=generator)
        state = create_retrieval_state(
            user_id="user-1",
            chat_id="chat-123",
            query="Compare net income and explain how profitability changed.",
            document_ids=["doc-1", "doc-1", "doc-2"],
        )

        result = await graph.ainvoke(
            state,
            config=retrieval_thread_config(chat_id="chat-123", user_id="user-1"),
        )

        self.assertEqual(generator.calls, 1)
        self.assertEqual(result["query_generation_attempts"], 1)
        self.assertFalse(result["query_generation_fallback"])
        self.assertEqual(result["document_ids"], ["doc-1", "doc-2"])
        self.assertEqual(result["retrieval_scope"], "normal")
        self.assertEqual(len(result["shared_queries"]), 2)
        self.assertEqual(len(result["text_queries"]), 2)
        self.assertEqual(len(result["table_queries"]), 2)
        self.assertEqual(result["metrics"], ["net income", "profit after tax"])
        self.assertEqual(result["column_terms"], ["year", "net income"])
        self.assertEqual(result["table_intent"], "required")
        self.assertEqual(result["match_concepts"][0]["canonical"], "net income")

    def test_thread_id_is_the_chat_id(self) -> None:
        config = retrieval_thread_config(chat_id="chat-123", user_id="user-1")

        self.assertEqual(config["configurable"]["thread_id"], "chat-123")
        self.assertEqual(config["metadata"]["user_id"], "user-1")

    def test_each_query_group_requires_two_to_three_unique_queries(self) -> None:
        with self.assertRaises(ValidationError):
            GeneratedRetrievalQueries(
                retrieval_scope=RetrievalScope.NORMAL,
                table_intent=TableIntent.REQUIRED,
                shared_queries=["same", "same"],
                text_queries=["text one", "text two"],
                table_queries=["table one", "table two"],
            )

    async def test_comprehensive_multi_facet_request_is_forced_broad(self) -> None:
        class BroadCorrectionGenerator:
            async def ainvoke(self, input: Any, **kwargs: Any) -> Any:
                return GeneratedRetrievalQueries(
                    retrieval_scope=RetrievalScope.NORMAL,
                    table_intent=TableIntent.REQUIRED,
                    shared_queries=["overall comparison", "annual comparison"],
                    text_queries=["performance discussion", "liquidity discussion"],
                    table_queries=["financial metrics table", "cash flow table"],
                    match_concepts=[
                        MatchConcept(
                            canonical=name,
                            kind=ConceptKind.METRIC,
                        )
                        for name in (
                            "financial performance",
                            "liquidity",
                            "expenses",
                            "cash flows",
                            "geographic revenue",
                        )
                    ],
                    metrics=[
                        "financial performance",
                        "liquidity",
                        "expenses",
                        "cash flows",
                        "geographic revenue",
                    ],
                )

        graph = build_query_generation_subgraph(
            query_generator=BroadCorrectionGenerator()
        )
        result = await graph.ainvoke(
            create_retrieval_state(
                user_id="user-1",
                chat_id="chat-broad",
                query=(
                    "Provide a comprehensive comparison of performance, liquidity, "
                    "expenses, cash flows, and geographic revenue."
                ),
                document_ids=["doc-1"],
            )
        )

        self.assertEqual(result["retrieval_scope"], "broad")

    async def test_all_selected_documents_is_broad_for_one_metric(self) -> None:
        class IncorrectNormalGenerator:
            async def ainvoke(self, input: Any, **kwargs: Any) -> Any:
                return GeneratedRetrievalQueries(
                    retrieval_scope=RetrievalScope.NORMAL,
                    table_intent=TableIntent.REQUIRED,
                    shared_queries=["net income all companies", "profit comparison"],
                    text_queries=["net income discussion", "profitability explanation"],
                    table_queries=["net income table", "annual profit columns"],
                    match_concepts=[
                        MatchConcept(
                            canonical="net income",
                            variants=["profit after tax"],
                            kind=ConceptKind.METRIC,
                        )
                    ],
                    metrics=["net income"],
                )

        graph = build_query_generation_subgraph(
            query_generator=IncorrectNormalGenerator()
        )
        result = await graph.ainvoke(
            create_retrieval_state(
                user_id="user-1",
                chat_id="chat-all-documents",
                query="Compare net income across all selected documents.",
                document_ids=["doc-1", "doc-2"],
            )
        )

        self.assertEqual(result["retrieval_scope"], "broad")

    async def test_malformed_structured_output_is_retried_once(self) -> None:
        valid = GeneratedRetrievalQueries(
            retrieval_scope=RetrievalScope.NORMAL,
            table_intent=TableIntent.NONE,
            shared_queries=["board chair", "board leadership"],
            text_queries=["chair and treasurer", "board member roles"],
            table_queries=["board roster", "leadership names"],
            match_concepts=[
                MatchConcept(
                    canonical="board chair",
                    kind=ConceptKind.ENTITY,
                )
            ],
        )

        class RecoveringGenerator:
            def __init__(self) -> None:
                self.calls = 0

            async def ainvoke(self, input: Any, **kwargs: Any) -> Any:
                self.calls += 1
                if self.calls == 1:
                    return {"retrieval_scope": "normal"}
                return valid

        generator = RecoveringGenerator()
        graph = build_query_generation_subgraph(query_generator=generator)
        result = await graph.ainvoke(
            create_retrieval_state(
                user_id="user-1",
                chat_id="chat-retry",
                query="Who chaired the board?",
                document_ids=["doc-1"],
            )
        )

        self.assertEqual(generator.calls, 2)
        self.assertEqual(result["table_intent"], "none")
        self.assertFalse(result["query_generation_fallback"])

    async def test_repeated_malformed_output_uses_deterministic_fallback(self) -> None:
        class InvalidGenerator:
            def __init__(self) -> None:
                self.calls = 0

            async def ainvoke(self, input: Any, **kwargs: Any) -> Any:
                self.calls += 1
                return {"retrieval_scope": "normal"}

        generator = InvalidGenerator()
        graph = build_query_generation_subgraph(query_generator=generator)
        result = await graph.ainvoke(
            create_retrieval_state(
                user_id="user-1",
                chat_id="chat-fallback",
                query="Explain the clean energy initiative.",
                document_ids=["doc-1"],
            )
        )

        self.assertEqual(generator.calls, 2)
        self.assertEqual(result["retrieval_scope"], "normal")
        self.assertEqual(result["table_intent"], "supporting")
        self.assertEqual(len(result["shared_queries"]), 2)
        self.assertTrue(result["query_generation_fallback"])

    async def test_bounded_four_metric_comparison_remains_normal(self) -> None:
        class FocusedGenerator:
            async def ainvoke(self, input: Any, **kwargs: Any) -> Any:
                names = [
                    "research and development expense",
                    "selling general and administrative expense",
                    "revenue",
                    "expense percentage of revenue",
                ]
                return GeneratedRetrievalQueries(
                    retrieval_scope=RetrievalScope.BROAD,
                    table_intent=TableIntent.REQUIRED,
                    shared_queries=["expense comparison", "expense ratios"],
                    text_queries=["expense discussion", "cost explanation"],
                    table_queries=["expense table", "expense percentage table"],
                    match_concepts=[
                        MatchConcept(
                            canonical=name,
                            kind=ConceptKind.METRIC,
                        )
                        for name in names
                    ],
                    metrics=names,
                    years=["2024", "2023"],
                )

        graph = build_query_generation_subgraph(query_generator=FocusedGenerator())
        result = await graph.ainvoke(
            create_retrieval_state(
                user_id="user-1",
                chat_id="chat-focused-scope",
                query=(
                    "Compare R&D and SG&A expenses as a percentage of revenue "
                    "in 2024 and 2023."
                ),
                document_ids=["doc-1"],
            )
        )

        self.assertEqual(result["retrieval_scope"], "normal")
