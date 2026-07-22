from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from scripts.data_analysis_agent.analysis.graph import build_data_analysis_graph
from scripts.data_analysis_agent.analysis.models import IssueCode
from scripts.data_analysis_agent.analysis.repositories import (
    EvidenceRepositoryError,
    HydrationSourceBatch,
    MongoEvidenceRepository,
)
from scripts.data_analysis_agent.analysis.state import (
    AnalysisPhase,
    analysis_thread_config,
    create_analysis_state,
)


DOCUMENT_ID = "a" * 64
SECOND_DOCUMENT_ID = "b" * 64


def _table(
    table_id: str = "table-1",
    *,
    user_id: str = "user-1",
    document_id: str = DOCUMENT_ID,
    title: str = "Revenue by year",
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "table_id": table_id,
        "document_id": document_id,
        "user_id": user_id,
        "page_start": 3,
        "page_end": 3,
        "title": title,
        "extraction_method": "pymupdf",
        "columns": [
            {"key": "year", "label": "Year", "type": "string"},
            {
                "key": "revenue",
                "label": "Revenue",
                "type": "number",
                "unit": "USD million",
            },
        ],
        "rows": rows if rows is not None else [{"year": "2024", "revenue": 120}],
        "source_fragments": [
            {"page": 3, "bounding_box": [10.0, 20.0, 300.0, 500.0]}
        ],
    }


def _document(document_id: str = DOCUMENT_ID) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "filename": "annual-report.pdf",
        "pages": 80,
        "ingestion_status": "ready",
        "table_ingestion_status": "ready",
    }


def _retrieval_table(
    table_id: str = "table-1",
    *,
    document_id: str = DOCUMENT_ID,
    title: str = "Revenue by year",
    query: str = "revenue 2024",
) -> dict[str, Any]:
    return {
        "table_id": table_id,
        "document_id": document_id,
        "title": title,
        "columns": ["year", "revenue"],
        "units": ["USD million"],
        "page_start": 3,
        "page_end": 3,
        "rrf_score": 0.08,
        "relevance_score": 0.91,
        "matched_queries": [query],
        "retrieval_modes": ["dense", "sparse"],
    }


class _FakeRetrievalGraph:
    def __init__(
        self,
        tables: list[dict[str, Any]] | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.tables = tables if tables is not None else [_retrieval_table()]
        self.fail = fail
        self.inputs: list[dict[str, Any]] = []

    async def ainvoke(self, input: Any, config: Any = None, **_kwargs: Any) -> Any:
        self.inputs.append(dict(input))
        if self.fail:
            raise RuntimeError("retrieval unavailable")
        return {
            **input,
            "retrieval_scope": "normal",
            "table_intent": "required",
            "metrics": ["revenue"],
            "years": ["2024"],
            "match_concepts": [
                {"canonical": "revenue", "variants": [], "kind": "metric"}
            ],
            "query_generation_attempts": 1,
            "query_generation_fallback": False,
            "final_text_chunks": [
                {
                    "chunk_id": "chunk-1",
                    "text": "Revenue increased in 2024.",
                    "metadata": {
                        "doc_id": DOCUMENT_ID,
                        "source": "annual-report.pdf",
                        "page_number": 4,
                    },
                    "relevance_score": 0.8,
                    "matched_queries": ["revenue 2024"],
                    "retrieval_modes": ["dense"],
                }
            ],
            "final_tables": self.tables,
        }


class _FakeEvidenceRepository:
    def __init__(
        self,
        *,
        tables: tuple[dict[str, Any], ...] | None = None,
        documents: tuple[dict[str, Any], ...] | None = None,
        fail: bool = False,
    ) -> None:
        self.tables = tables if tables is not None else (_table(),)
        self.documents = documents if documents is not None else (_document(),)
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def load_sources(self, **kwargs: Any) -> HydrationSourceBatch:
        self.calls.append(kwargs)
        if self.fail:
            raise EvidenceRepositoryError("database unavailable")
        return HydrationSourceBatch(
            tables=self.tables,
            documents=self.documents,
        )


class ParentStateTests(unittest.TestCase):
    def test_initial_state_is_minimal_validated_and_run_scoped(self) -> None:
        run_id = str(uuid4())
        state = create_analysis_state(
            user_id=" user-1 ",
            chat_id=" chat-1 ",
            query="  compare   revenue ",
            document_ids=[DOCUMENT_ID.upper(), DOCUMENT_ID],
            run_id=run_id,
        )

        self.assertEqual(
            set(state),
            {
                "state_version",
                "run_id",
                "request",
                "phase",
                "warnings",
                "errors",
            },
        )
        self.assertEqual(state["request"].document_ids, (DOCUMENT_ID,))
        self.assertEqual(state["request"].query, "compare revenue")
        self.assertEqual(state["phase"], AnalysisPhase.INITIALIZED)

        config = analysis_thread_config(state)
        self.assertEqual(config["configurable"]["thread_id"], run_id)
        self.assertEqual(config["metadata"]["chat_id"], "chat-1")

    def test_non_sha_document_identity_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            create_analysis_state(
                user_id="user-1",
                chat_id="chat-1",
                query="compare revenue",
                document_ids=["mongo-object-id"],
            )


class ParentAnalysisGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_retrieves_and_hydrates_without_checkpointing_rows(self) -> None:
        retrieval = _FakeRetrievalGraph()
        repository = _FakeEvidenceRepository()
        graph = build_data_analysis_graph(
            retrieval_graph=retrieval,
            evidence_repository=repository,
        )
        state = create_analysis_state(
            user_id="user-1",
            chat_id="chat-1",
            query="compare revenue",
            document_ids=[DOCUMENT_ID],
        )

        result = await graph.ainvoke(state, config=analysis_thread_config(state))

        self.assertEqual(result["phase"], AnalysisPhase.HYDRATED)
        self.assertEqual(result["evidence_package"].status, "complete")
        self.assertEqual(result["evidence_package"].hydrated_table_count, 1)
        dataset = result["evidence_package"].datasets[0]
        self.assertEqual(dataset.table_id, "table-1")
        self.assertEqual(dataset.document_name, "annual-report.pdf")
        self.assertEqual(dataset.row_count, 1)
        self.assertTrue(dataset.usable_for_analysis)
        self.assertEqual(dataset.access.table_id, "table-1")
        self.assertNotIn("rows", dataset.model_dump())

        self.assertEqual(len(repository.calls), 1)
        self.assertEqual(repository.calls[0]["table_ids"], ("table-1",))
        self.assertEqual(repository.calls[0]["document_ids"], (DOCUMENT_ID,))
        self.assertNotIn("shared_queries", result)
        self.assertNotIn("retrieved_tables", result)

    async def test_dataset_identity_is_stable_for_unchanged_source_content(self) -> None:
        graph = build_data_analysis_graph(
            retrieval_graph=_FakeRetrievalGraph(),
            evidence_repository=_FakeEvidenceRepository(),
        )
        first_state = create_analysis_state(
            user_id="user-1",
            chat_id="chat-1",
            query="compare revenue",
            document_ids=[DOCUMENT_ID],
        )
        second_state = create_analysis_state(
            user_id="user-1",
            chat_id="chat-1",
            query="compare revenue",
            document_ids=[DOCUMENT_ID],
        )

        first, second = await graph.ainvoke(first_state), await graph.ainvoke(
            second_state
        )

        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(
            first["evidence_package"].datasets[0].dataset_id,
            second["evidence_package"].datasets[0].dataset_id,
        )

    async def test_duplicate_references_are_merged_and_missing_tables_are_partial(self) -> None:
        retrieval = _FakeRetrievalGraph(
            tables=[
                _retrieval_table(query="query one"),
                _retrieval_table(query="query two"),
                _retrieval_table("missing-table"),
            ]
        )
        repository = _FakeEvidenceRepository()
        graph = build_data_analysis_graph(
            retrieval_graph=retrieval,
            evidence_repository=repository,
        )
        state = create_analysis_state(
            user_id="user-1",
            chat_id="chat-1",
            query="compare revenue",
            document_ids=[DOCUMENT_ID],
        )

        result = await graph.ainvoke(state)

        package = result["evidence_package"]
        self.assertEqual(package.status, "partial")
        self.assertEqual(package.retrieved_table_count, 2)
        self.assertEqual(package.hydrated_table_count, 1)
        self.assertEqual(
            package.datasets[0].matched_queries,
            ("query one", "query two"),
        )
        self.assertEqual(package.unresolved_tables[0].table_id, "missing-table")
        self.assertIn(IssueCode.TABLE_NOT_AVAILABLE, [w.code for w in result["warnings"]])
        self.assertEqual(
            repository.calls[0]["table_ids"],
            ("table-1", "missing-table"),
        )

    async def test_authoritative_metadata_replaces_stale_retrieval_metadata(self) -> None:
        graph = build_data_analysis_graph(
            retrieval_graph=_FakeRetrievalGraph(
                tables=[_retrieval_table(title="Old title")]
            ),
            evidence_repository=_FakeEvidenceRepository(),
        )
        state = create_analysis_state(
            user_id="user-1",
            chat_id="chat-1",
            query="compare revenue",
            document_ids=[DOCUMENT_ID],
        )

        result = await graph.ainvoke(state)

        self.assertEqual(
            result["evidence_package"].datasets[0].title,
            "Revenue by year",
        )
        self.assertIn(
            IssueCode.STALE_RETRIEVAL_METADATA,
            [warning.code for warning in result["warnings"]],
        )

    async def test_hydrator_defensively_rejects_cross_tenant_source_records(self) -> None:
        graph = build_data_analysis_graph(
            retrieval_graph=_FakeRetrievalGraph(),
            evidence_repository=_FakeEvidenceRepository(
                tables=(_table(user_id="another-user"),)
            ),
        )
        state = create_analysis_state(
            user_id="user-1",
            chat_id="chat-1",
            query="compare revenue",
            document_ids=[DOCUMENT_ID],
        )

        result = await graph.ainvoke(state)

        self.assertEqual(result["evidence_package"].status, "empty")
        self.assertEqual(result["evidence_package"].datasets, ())
        self.assertEqual(
            result["evidence_package"].unresolved_tables[0].reason,
            "not_available",
        )
        self.assertIn(
            IssueCode.TABLE_NOT_AVAILABLE,
            [warning.code for warning in result["warnings"]],
        )

    async def test_retrieval_failure_stops_before_hydration(self) -> None:
        repository = _FakeEvidenceRepository()
        graph = build_data_analysis_graph(
            retrieval_graph=_FakeRetrievalGraph(fail=True),
            evidence_repository=repository,
        )
        state = create_analysis_state(
            user_id="user-1",
            chat_id="chat-1",
            query="compare revenue",
            document_ids=[DOCUMENT_ID],
        )

        with patch(
            "scripts.data_analysis_agent.analysis.nodes.retrieve.logger.exception"
        ):
            result = await graph.ainvoke(state)

        self.assertEqual(result["phase"], AnalysisPhase.FAILED)
        self.assertEqual(result["errors"][0].code, IssueCode.RETRIEVAL_FAILED)
        self.assertEqual(repository.calls, [])

    async def test_repository_failure_returns_failed_evidence_package(self) -> None:
        graph = build_data_analysis_graph(
            retrieval_graph=_FakeRetrievalGraph(),
            evidence_repository=_FakeEvidenceRepository(fail=True),
        )
        state = create_analysis_state(
            user_id="user-1",
            chat_id="chat-1",
            query="compare revenue",
            document_ids=[DOCUMENT_ID],
        )

        with patch(
            "scripts.data_analysis_agent.analysis.nodes.hydrate.logger.exception"
        ):
            result = await graph.ainvoke(state)

        self.assertEqual(result["phase"], AnalysisPhase.FAILED)
        self.assertEqual(result["evidence_package"].status, "failed")
        self.assertEqual(result["errors"][0].code, IssueCode.HYDRATION_FAILED)


class _Cursor:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values
        self.length: int | None = None

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        self.length = length
        return self.values[:length]


class _Collection:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def find(
        self,
        query: dict[str, Any],
        projection: dict[str, Any],
    ) -> _Cursor:
        self.calls.append((query, projection))
        return _Cursor(self.values)


class _Database:
    def __init__(self) -> None:
        self.structured_tables = _Collection([_table()])
        self.documents = _Collection([_document()])


class MongoEvidenceRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_queries_enforce_tenant_and_document_scope(self) -> None:
        database = _Database()
        repository = MongoEvidenceRepository()

        with patch(
            "scripts.data_analysis_agent.analysis.repositories.evidence.get_db",
            return_value=database,
        ):
            batch = await repository.load_sources(
                user_id="user-1",
                document_ids=[DOCUMENT_ID, SECOND_DOCUMENT_ID],
                table_ids=["table-1", "table-2"],
            )

        table_query = database.structured_tables.calls[0][0]
        document_query = database.documents.calls[0][0]
        self.assertEqual(table_query["user_id"], "user-1")
        self.assertEqual(
            table_query["document_id"],
            {"$in": [DOCUMENT_ID, SECOND_DOCUMENT_ID]},
        )
        self.assertEqual(table_query["table_id"], {"$in": ["table-1", "table-2"]})
        self.assertEqual(document_query["user_id"], "user-1")
        self.assertEqual(len(batch.tables), 1)
        self.assertEqual(len(batch.documents), 1)


if __name__ == "__main__":
    unittest.main()
