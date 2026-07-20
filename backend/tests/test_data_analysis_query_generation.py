from __future__ import annotations

import unittest
from typing import Any

from pydantic import ValidationError

from scripts.data_analysis_agent.reterival.query_generation import (
    GeneratedRetrievalQueries,
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
        self.assertEqual(result["document_ids"], ["doc-1", "doc-2"])
        self.assertEqual(len(result["shared_queries"]), 2)
        self.assertEqual(len(result["text_queries"]), 2)
        self.assertEqual(len(result["table_queries"]), 2)

    def test_thread_id_is_the_chat_id(self) -> None:
        config = retrieval_thread_config(chat_id="chat-123", user_id="user-1")

        self.assertEqual(config["configurable"]["thread_id"], "chat-123")
        self.assertEqual(config["metadata"]["user_id"], "user-1")

    def test_each_query_group_requires_two_to_three_unique_queries(self) -> None:
        with self.assertRaises(ValidationError):
            GeneratedRetrievalQueries(
                shared_queries=["same", "same"],
                text_queries=["text one", "text two"],
                table_queries=["table one", "table two"],
            )
