from __future__ import annotations

import unittest
from typing import Any

from scripts.data_analysis_agent.reterival.fusion import (
    DeterministicResultSelector,
)
from scripts.data_analysis_agent.reterival.state import create_retrieval_state
from scripts.data_analysis_agent.reterival.utils.diversity import (
    select_tables,
    select_text_chunks,
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
            shared_queries=["Acme annual financial performance", "FY2023 results"],
            text_queries=["Acme profitability discussion", "net income trend"],
            table_queries=["net income by year", "income statement columns"],
            metrics=["net income"],
            years=["FY2023"],
            entities=["Acme"],
            units=["USD million"],
            column_terms=["net income"],
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
        self.assertGreater(
            result["final_tables"][0]["relevance_score"],
            result["final_tables"][1]["relevance_score"],
        )
        self.assertEqual(
            result["final_tables"][0]["relevance_features"]["metric"],
            1.0,
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
