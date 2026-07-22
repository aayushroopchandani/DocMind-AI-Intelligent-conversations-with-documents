from __future__ import annotations

import unittest
from typing import Any

from scripts.data_analysis_agent.reterival.fusion import (
    DeterministicResultSelector,
)
from scripts.data_analysis_agent.reterival.state import create_retrieval_state
from scripts.data_analysis_agent.reterival.utils.concepts import (
    acronym_matches,
    concept_coverage,
    concept_specificities,
    phrase_match_strength,
    RetrievalConcept,
)
from scripts.data_analysis_agent.reterival.utils.diversity import (
    select_tables,
    select_text_chunks,
)
from scripts.data_analysis_agent.reterival.utils.relevance import (
    RetrievalSignals,
    build_scoring_context,
    text_candidate_content,
)


def _text_candidate(
    chunk_id: str,
    *,
    text: str,
    document_id: str = "doc-1",
    page: int = 1,
    score: float = 0.5,
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "text": text,
        "metadata": {"doc_id": document_id, "page_number": page},
        "relevance_score": score,
    }


def _table_candidate(
    table_id: str,
    *,
    title: str,
    columns: list[str],
    document_id: str = "doc-1",
    page: int = 1,
    score: float = 0.5,
) -> dict[str, Any]:
    return {
        "table_id": table_id,
        "title": title,
        "columns": columns,
        "document_id": document_id,
        "page_start": page,
        "page_end": page,
        "relevance_score": score,
    }


class DeterministicFusionTests(unittest.TestCase):
    def test_metric_year_column_and_unit_signals_can_correct_rrf_order(self) -> None:
        state = create_retrieval_state(
            user_id="user-1",
            chat_id="chat-1",
            query="What was Acme net income in FY2023 in USD million?",
            document_ids=["doc-1"],
        )
        state.update(
            retrieval_scope="normal",
            table_intent="required",
            shared_queries=["Acme annual financial performance", "FY2023 results"],
            text_queries=["Acme profitability discussion", "net income trend"],
            table_queries=["net income by year", "income statement columns"],
            metrics=["net income"],
            years=["FY2023"],
            entities=["Acme"],
            units=["USD million"],
            column_terms=["net income"],
            match_concepts=[
                {
                    "canonical": "net income",
                    "variants": ["profit after tax"],
                    "kind": "metric",
                }
            ],
            retrieved_text_chunks=[],
            retrieved_tables=[
                {
                    "table_id": "revenue",
                    "document_id": "doc-1",
                    "rrf_score": 0.16,
                    "title": "Annual revenue",
                    "summary": "Acme revenue for FY2023",
                    "columns": ["year", "revenue"],
                    "metrics": ["revenue"],
                    "units": ["USD million"],
                    "keywords": [],
                    "page_start": 10,
                    "page_end": 10,
                    "matched_queries": ["FY2023 results"],
                    "retrieval_modes": ["dense", "sparse"],
                },
                {
                    "table_id": "net-income",
                    "document_id": "doc-1",
                    "rrf_score": 0.07,
                    "title": "Consolidated income statement",
                    "summary": "Acme net income for FY2023",
                    "columns": ["year", "net_income"],
                    "metrics": ["net_income"],
                    "units": ["USD million"],
                    "keywords": ["profitability"],
                    "page_start": 20,
                    "page_end": 20,
                    "matched_queries": ["net income by year"],
                    "retrieval_modes": ["dense", "sparse"],
                },
            ],
        )

        result = DeterministicResultSelector().select(state)

        self.assertEqual(result["final_tables"][0]["table_id"], "net-income")
        self.assertNotIn(
            "revenue",
            {table["table_id"] for table in result["final_tables"]},
        )
        self.assertGreater(
            result["final_tables"][0]["relevance_features"]["concept"],
            0.5,
        )
        self.assertEqual(
            result["final_tables"][0]["relevance_features"]["consensus"],
            0.0,
        )

    def test_generic_acronym_matching_requires_no_financial_alias_map(self) -> None:
        self.assertTrue(acronym_matches("R&D", "research and development"))
        self.assertTrue(
            acronym_matches(
                "SG&A",
                "selling, general and administrative",
            )
        )
        self.assertTrue(acronym_matches("EPS", "earnings per share"))
        self.assertTrue(acronym_matches("PAT", "profit after tax"))
        self.assertTrue(acronym_matches("DTA", "deferred tax assets"))
        self.assertTrue(acronym_matches("earnings per share", "EPS"))

    def test_layout_proximity_matches_customer_identifier_without_matching_article(self) -> None:
        self.assertGreater(
            phrase_match_strength(
                "Customer A",
                "Customer 2024 2023 2022 A 19 percent",
            ),
            0,
        )
        self.assertEqual(
            phrase_match_strength(
                "Customer A",
                "The customer accepted a product.",
            ),
            0,
        )

    def test_candidate_prevalence_downweights_generic_concepts(self) -> None:
        concepts = (
            RetrievalConcept("revenue"),
            RetrievalConcept("geographic revenue"),
        )
        weights = concept_specificities(
            concepts,
            [
                "annual revenue",
                "deferred revenue",
                "geographic revenue by country",
                "revenue recognition",
            ],
        )

        self.assertLess(weights[0], weights[1])

    def test_multiple_aliases_count_as_one_concept(self) -> None:
        concept = RetrievalConcept(
            "research and development expense",
            ("R&D expense", "research and development cost"),
        )

        self.assertEqual(
            concept_coverage(
                [concept],
                "The research and development expense increased.",
                specificities=[1.0],
            ),
            1.0,
        )

    def test_consensus_shared_by_most_candidates_has_little_influence(self) -> None:
        common = [
            {
                "chunk_id": f"common-{index}",
                "matched_queries": [f"query-{query}" for query in range(5)],
                "retrieval_modes": ["dense", "sparse"],
            }
            for index in range(5)
        ]
        outlier = {
            "chunk_id": "outlier",
            "matched_queries": ["query-0"],
            "retrieval_modes": ["dense"],
        }
        context = build_scoring_context(
            [*common, outlier],
            signals=RetrievalSignals(),
            query_count=5,
            candidate_text=text_candidate_content,
        )

        self.assertLess(context.consensus(common[0]), 0.1)

    def test_narrative_intent_returns_no_tables(self) -> None:
        state = create_retrieval_state(
            user_id="user-1",
            chat_id="chat-narrative",
            query="Who chaired the ACT-Brasil board?",
            document_ids=["doc-1"],
        )
        state.update(
            retrieval_scope="normal",
            table_intent="none",
            shared_queries=["ACT Brasil board", "board leadership"],
            text_queries=["ACT Brasil chair", "board officer names"],
            table_queries=["board roster", "leadership table"],
            match_concepts=[
                {
                    "canonical": "ACT-Brasil board chair",
                    "variants": ["chair of the ACT Brasil board"],
                    "kind": "entity",
                }
            ],
            retrieved_text_chunks=[
                {
                    "chunk_id": "board",
                    "rrf_score": 0.10,
                    "text": "ACT-Brasil Board of Directors Sandra Charity Chair",
                    "metadata": {"doc_id": "doc-1", "page_number": 32},
                    "matched_queries": ["ACT Brasil board", "ACT Brasil chair"],
                    "retrieval_modes": ["dense", "sparse"],
                }
            ],
            retrieved_tables=[
                {
                    "table_id": "financial-position",
                    "document_id": "doc-1",
                    "rrf_score": 0.16,
                    "title": "Financial position",
                    "summary": "Assets and liabilities for 2023",
                    "columns": ["asset", "2023"],
                    "page_start": 25,
                    "page_end": 25,
                    "matched_queries": ["board roster", "leadership table"],
                    "retrieval_modes": ["dense", "sparse"],
                }
            ],
        )

        result = DeterministicResultSelector().select(state)

        self.assertEqual(result["final_tables"], [])
        self.assertEqual(result["final_text_chunks"][0]["chunk_id"], "board")

    def test_focused_text_selection_drops_weak_generic_tail(self) -> None:
        state = create_retrieval_state(
            user_id="user-1",
            chat_id="chat-focused",
            query="How did ACT bring clean energy to remote communities?",
            document_ids=["doc-1"],
        )
        state.update(
            retrieval_scope="normal",
            table_intent="none",
            shared_queries=["clean energy communities", "remote energy project"],
            text_queries=["clean energy initiative", "community energy access"],
            table_queries=["energy project table", "community energy metrics"],
            match_concepts=[
                {
                    "canonical": "clean energy",
                    "variants": ["renewable energy"],
                    "kind": "topic",
                },
                {
                    "canonical": "remote communities",
                    "kind": "entity",
                },
            ],
            retrieved_text_chunks=[
                {
                    "chunk_id": "clean-energy",
                    "rrf_score": 0.10,
                    "text": "The clean energy project served remote communities.",
                    "metadata": {"doc_id": "doc-1", "page_number": 13},
                    "matched_queries": [
                        "clean energy communities",
                        "clean energy initiative",
                    ],
                    "retrieval_modes": ["dense", "sparse"],
                },
                *[
                    {
                        "chunk_id": f"generic-{index}",
                        "rrf_score": 0.09 - (index / 1000),
                        "text": f"General annual report discussion section {index}.",
                        "metadata": {
                            "doc_id": "doc-1",
                            "page_number": index + 20,
                        },
                        "matched_queries": ["remote energy project"],
                        "retrieval_modes": ["dense", "sparse"],
                    }
                    for index in range(5)
                ],
            ],
            retrieved_tables=[],
        )

        result = DeterministicResultSelector().select(state)

        self.assertEqual(
            [chunk["chunk_id"] for chunk in result["final_text_chunks"]],
            ["clean-energy"],
        )

    def test_expanded_metric_tables_outrank_generic_revenue_table(self) -> None:
        state = create_retrieval_state(
            user_id="user-1",
            chat_id="chat-acronyms",
            query="Compare R&D and SG&A expenses as a percentage of revenue.",
            document_ids=["doc-1"],
        )
        state.update(
            retrieval_scope="normal",
            table_intent="required",
            shared_queries=["operating expense comparison", "expense ratios"],
            text_queries=["R&D SG&A discussion", "expense ratio explanation"],
            table_queries=["R&D SG&A table", "expense percentage columns"],
            match_concepts=[
                {
                    "canonical": "research and development expense",
                    "variants": ["R&D expense"],
                    "kind": "metric",
                },
                {
                    "canonical": "selling general and administrative expense",
                    "variants": ["SG&A expense"],
                    "kind": "metric",
                },
                {
                    "canonical": "percentage of revenue",
                    "variants": ["revenue percentage"],
                    "kind": "metric",
                },
            ],
            years=["2024", "2023"],
            retrieved_text_chunks=[],
            retrieved_tables=[
                {
                    "table_id": "generic-revenue",
                    "document_id": "doc-1",
                    "rrf_score": 0.16,
                    "title": "Revenue and gross margin",
                    "summary": "Revenue percentages for 2024 and 2023",
                    "columns": ["year", "revenue", "percentage"],
                    "page_start": 44,
                    "page_end": 44,
                    "matched_queries": ["operating expense comparison"],
                    "retrieval_modes": ["dense", "sparse"],
                },
                {
                    "table_id": "research-development",
                    "document_id": "doc-1",
                    "rrf_score": 0.10,
                    "title": "Research and development",
                    "summary": "R&D expense as a percentage of revenue for 2024 and 2023",
                    "columns": ["research_and_development", "percentage_of_revenue"],
                    "page_start": 45,
                    "page_end": 45,
                    "matched_queries": ["R&D SG&A table"],
                    "retrieval_modes": ["dense", "sparse"],
                },
                {
                    "table_id": "selling-administrative",
                    "document_id": "doc-1",
                    "rrf_score": 0.10,
                    "title": "Selling, general, and administrative",
                    "summary": "SG&A expense as a percentage of revenue for 2024 and 2023",
                    "columns": ["selling_general_administrative", "percentage_of_revenue"],
                    "page_start": 46,
                    "page_end": 46,
                    "matched_queries": ["R&D SG&A table"],
                    "retrieval_modes": ["dense", "sparse"],
                },
            ],
        )

        result = DeterministicResultSelector().select(state)

        self.assertEqual(
            {table["table_id"] for table in result["final_tables"][:2]},
            {"research-development", "selling-administrative"},
        )

    def test_text_selection_removes_duplicates_and_caps_same_page(self) -> None:
        candidates = [
            _text_candidate(
                "original",
                text="Net income for 2023 increased according to the annual report.",
                page=1,
                score=1.0,
            ),
            _text_candidate(
                "duplicate",
                text="Net income for 2023 increased according to the annual report.",
                page=1,
                score=0.99,
            ),
            _text_candidate(
                "page-two-a",
                text="Net income in 2023 appears in the audited earnings schedule alpha.",
                page=2,
                score=0.95,
            ),
            _text_candidate(
                "page-two-b",
                text="Audited 2023 net income includes a quarterly comparison beta.",
                page=2,
                score=0.94,
            ),
            _text_candidate(
                "page-two-c",
                text="The company reported 2023 net income and variance gamma.",
                page=2,
                score=0.93,
            ),
            *[
                _text_candidate(
                    f"other-{index}",
                    text=f"Net income evidence for 2023 from section {index}.",
                    page=index + 2,
                    score=0.9 - (index / 100),
                )
                for index in range(1, 6)
            ],
        ]

        selected = select_text_chunks(candidates, limit=6, broad=False)
        selected_ids = {item["chunk_id"] for item in selected}

        self.assertEqual(len(selected), 6)
        self.assertNotIn("duplicate", selected_ids)
        self.assertNotIn("page-two-c", selected_ids)

    def test_table_selection_removes_same_page_structural_duplicates(self) -> None:
        candidates = [
            _table_candidate(
                "pymupdf-table",
                title="Consolidated income statement",
                columns=["year", "revenue", "net income"],
                page=40,
                score=1.0,
            ),
            _table_candidate(
                "docling-table",
                title="Consolidated Income Statement",
                columns=["Year", "Revenue", "Net Income"],
                page=40,
                score=0.98,
            ),
            _table_candidate(
                "balance-sheet",
                title="Consolidated balance sheet",
                columns=["year", "assets", "liabilities"],
                page=41,
                score=0.9,
            ),
        ]

        selected = select_tables(candidates, limit=4, broad=False)

        self.assertEqual(
            [item["table_id"] for item in selected],
            ["pymupdf-table", "balance-sheet"],
        )

    def test_broad_selection_round_robins_documents(self) -> None:
        candidates = [
            _text_candidate(
                f"doc-one-{index}",
                text=f"Distinct evidence from document one section {index}.",
                document_id="doc-1",
                page=index,
                score=1.0 - (index / 100),
            )
            for index in range(1, 5)
        ] + [
            _text_candidate(
                "doc-two-1",
                text="Distinct evidence from document one section 1.",
                document_id="doc-2",
                page=1,
                score=0.7,
            )
        ]

        selected = select_text_chunks(candidates, limit=3, broad=True)

        self.assertEqual(
            [item["metadata"]["doc_id"] for item in selected],
            ["doc-1", "doc-2", "doc-1"],
        )


if __name__ == "__main__":
    unittest.main()
