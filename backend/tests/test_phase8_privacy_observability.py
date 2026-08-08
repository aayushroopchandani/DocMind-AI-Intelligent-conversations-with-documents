from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from uuid import uuid4

from pydantic import ValidationError

from scripts.data_analysis_agent.runtime.models.events import (
    AnalysisEventType,
    AnalysisRunEvent,
)
from scripts.data_analysis_agent.runtime.models.privacy import (
    AnalysisPrivacyMode,
    DataSensitivity,
)
from scripts.data_analysis_agent.runtime.models.runs import (
    AnalysisRunPhase,
    AnalysisRunStatus,
    StageTokenUsage,
    TokenUsage,
)
from scripts.data_analysis_agent.runtime.models.workbook import (
    SpreadsheetContext,
    WorkbookRangeSnapshot,
    canonical_snapshot_hash,
)
from scripts.data_analysis_agent.runtime.observability.logging import (
    AnalysisJsonFormatter,
    get_analysis_logger,
    log_analysis_event,
    safe_analysis_dimensions,
)
from scripts.data_analysis_agent.runtime.observability.metrics import (
    AnalysisMetrics,
)
from scripts.data_analysis_agent.runtime.privacy import PrivacyGateway
from scripts.data_analysis_agent.runtime.services.diagnostics import (
    AnalysisDiagnosticsService,
)
from scripts.data_analysis_agent.runtime.services.workbook_context import (
    _snapshot_to_tabular,
)


class PrivacyGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = PrivacyGateway()

    def test_sensitive_columns_never_expose_examples(self) -> None:
        result = self.gateway.sanitize_examples(
            column_key="customer_email",
            label="Email address",
            semantic_role="dimension",
            values=("alice@example.com", "bob@example.com"),
            mode=AnalysisPrivacyMode.STANDARD,
        )

        self.assertEqual(result.classification, DataSensitivity.EMAIL)
        self.assertEqual(result.examples, ())
        self.assertEqual(result.redacted_count, 2)

    def test_schema_and_local_modes_remove_all_representative_values(self) -> None:
        for mode in (
            AnalysisPrivacyMode.SCHEMA_ONLY,
            AnalysisPrivacyMode.LOCAL_ONLY,
        ):
            with self.subTest(mode=mode):
                result = self.gateway.sanitize_examples(
                    column_key="revenue",
                    label="Revenue",
                    semantic_role="metric",
                    values=("50000", "75000"),
                    mode=mode,
                )
                self.assertEqual(result.classification, DataSensitivity.NONE)
                self.assertEqual(result.examples, ())
                self.assertEqual(result.redacted_count, 2)

    def test_credential_patterns_are_redacted_deterministically(self) -> None:
        text = "connection=mongodb://user:password@example.test/db"

        redacted = self.gateway.redact_sensitive_text(text)

        self.assertNotIn("password", redacted)
        self.assertIn("[REDACTED:CREDENTIAL]", redacted)

    def test_signed_provider_urls_are_never_retained(self) -> None:
        url = (
            "https://res.cloudinary.com/demo/raw/upload/file.csv"
            "?token=private-provider-token"
        )

        redacted = self.gateway.redact_sensitive_text(url)

        self.assertEqual(redacted, "[REDACTED:SIGNED_URL]")


class DurableEventPrivacyTests(unittest.TestCase):
    def _event(self, payload: dict[str, object]) -> AnalysisRunEvent:
        return AnalysisRunEvent(
            run_id=str(uuid4()),
            user_id="user-1",
            workspace_id="workspace-1",
            sequence=1,
            event_type=AnalysisEventType.RUN_FAILED,
            status=AnalysisRunStatus.FAILED,
            phase=AnalysisRunPhase.COMPLETED,
            payload=payload,
        )

    def test_raw_rows_and_formulas_are_rejected(self) -> None:
        for key in ("rows", "values", "formulas", "prompt"):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                self._event({key: ["sensitive"]})

    def test_sensitive_free_text_is_redacted_before_persistence(self) -> None:
        event = self._event(
            {"message": "Contact alice@example.com or +1 415-555-0123"}
        )

        message = str(event.payload["message"])
        self.assertNotIn("alice@example.com", message)
        self.assertIn("[REDACTED:EMAIL]", message)


class SafeLoggingTests(unittest.TestCase):
    def test_logging_contract_is_allow_listed_and_redacts_values(self) -> None:
        with self.assertRaises(ValueError):
            safe_analysis_dimensions({"workbook_rows": [["secret"]]})

        logger = logging.getLogger(f"phase8-test-{uuid4()}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        records: list[logging.LogRecord] = []

        class _Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = _Handler()
        logger.addHandler(handler)
        try:
            log_analysis_event(
                logger,
                "run_failed",
                run_id="alice@example.com",
                workspace_id="workspace-1",
                operation="planning",
                error_code="planner_unavailable",
            )
        finally:
            logger.removeHandler(handler)

        self.assertEqual(len(records), 1)
        payload = records[0].analysis
        self.assertNotIn("alice@example.com", str(payload))
        self.assertNotIn("rows", payload)

    def test_pipeline_logger_redacts_message_and_exception_values(self) -> None:
        logger = get_analysis_logger(f"phase8-private-test-{uuid4()}")
        logger.setLevel(logging.ERROR)
        logger.propagate = False
        rendered: list[str] = []

        class _Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                rendered.append(self.format(record))

        handler = _Handler()
        handler.setFormatter(logging.Formatter("%(message)s %(exc_text)s"))
        logger.addHandler(handler)
        try:
            sensitive_error = "cell value " + "alice@example.com"
            try:
                raise RuntimeError(sensitive_error)
            except RuntimeError:
                logger.exception("processing alice@example.com failed")
        finally:
            logger.removeHandler(handler)

        output = "\n".join(rendered)
        self.assertNotIn("alice@example.com", output)
        self.assertNotIn("cell value", output)
        self.assertIn("[REDACTED:EMAIL]", output)
        self.assertIn("details redacted", output)

    def test_json_formatter_emits_structured_safe_dimensions(self) -> None:
        record = logging.LogRecord(
            name="scripts.data_analysis_agent.runtime.services.worker",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="analysis_event",
            args=(),
            exc_info=None,
        )
        record.analysis = {
            "event": "run_completed",
            "run_id": "run-1",
            "row_count": 10,
        }

        payload = AnalysisJsonFormatter().format(record)

        self.assertIn('"message":"analysis_event"', payload)
        self.assertIn('"row_count":10', payload)
        self.assertNotIn("workbook_rows", payload)


class WorkbookPrivacyTests(unittest.TestCase):
    def _snapshot(self) -> WorkbookRangeSnapshot:
        return WorkbookRangeSnapshot(
            range_a1="Sheet1!A1:C4",
            values=(
                ("Name", "Revenue", "Secret"),
                ("Visible A", 50_000, "one"),
                ("Hidden", 60_000, "two"),
                ("Visible B", 70_000, "three"),
            ),
            formulas=((None, None, None),) * 4,
            cell_types=(
                ("string", "string", "string"),
                ("string", "number", "string"),
                ("string", "number", "string"),
                ("string", "number", "string"),
            ),
            number_formats=((None, None, None),) * 4,
            column_headers=("Name", "Revenue", "Secret"),
            header_row_index=0,
            row_count=4,
            column_count=3,
            hidden_rows=(2,),
            hidden_columns=(2,),
        )

    def _context(
        self,
        snapshot: WorkbookRangeSnapshot,
        *,
        selected: bool,
    ) -> SpreadsheetContext:
        return SpreadsheetContext(
            workbook_id="workbook-1",
            workbook_name="Book",
            client_revision=2,
            worksheet_id="sheet-1",
            worksheet_name="Sheet1",
            selected_range=(snapshot.range_a1 if selected else None),
            used_range=snapshot.range_a1,
            snapshot_range=snapshot.range_a1,
            snapshot_hash=canonical_snapshot_hash(snapshot),
            snapshot=snapshot,
        )

    def test_automatic_used_range_excludes_hidden_rows_and_columns(self) -> None:
        snapshot = self._snapshot()

        dataset = _snapshot_to_tabular(
            user_id="user-1",
            workspace_id="workspace-1",
            artifact_id="artifact-1",
            artifact_version_id="version-1",
            context=self._context(snapshot, selected=False),
            snapshot=snapshot,
            max_columns=500,
        )

        self.assertEqual([column.source_index for column in dataset.columns], [0, 1])
        self.assertEqual(len(dataset.rows), 2)
        self.assertEqual(dataset.locator.hidden_rows_excluded, 1)
        self.assertEqual(dataset.locator.hidden_columns_excluded, 1)

    def test_explicit_selection_retains_hidden_cells_as_user_scope(self) -> None:
        snapshot = self._snapshot()

        dataset = _snapshot_to_tabular(
            user_id="user-1",
            workspace_id="workspace-1",
            artifact_id="artifact-1",
            artifact_version_id="version-1",
            context=self._context(snapshot, selected=True),
            snapshot=snapshot,
            max_columns=500,
        )

        self.assertEqual(len(dataset.columns), 3)
        self.assertEqual(len(dataset.rows), 3)
        self.assertEqual(dataset.locator.hidden_rows_excluded, 0)


class _AggregateCursor:
    async def to_list(self, *, length: int) -> list[dict[str, object]]:
        del length
        return [{"_id": "created", "count": 2}]


class _RunsCollection:
    async def count_documents(self, _query: object) -> int:
        return 2

    def aggregate(self, _pipeline: object) -> _AggregateCursor:
        return _AggregateCursor()


class _DiagnosticsDatabase:
    analysis_runs = _RunsCollection()

    async def command(self, name: str) -> dict[str, int]:
        if name != "ping":
            raise AssertionError(name)
        return {"ok": 1}


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_reports_worker_queue_and_process_metrics(self) -> None:
        metrics = AnalysisMetrics()
        metrics.observe_phase("planning", 20)
        metrics.observe_llm(
            StageTokenUsage(
                stage="planning",
                model="test-model",
                prompt_version="1",
                usage=TokenUsage(
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                ),
            )
        )
        worker = SimpleNamespace(
            running=True,
            active_run_count=1,
            concurrency=2,
        )
        service = AnalysisDiagnosticsService(
            database=_DiagnosticsDatabase(),
            worker=worker,
            metrics=metrics,
        )

        snapshot = await service.snapshot()

        self.assertTrue(snapshot.ready)
        self.assertEqual(snapshot.queue_depth, 2)
        self.assertEqual(snapshot.runs_by_status, {"created": 2})
        self.assertEqual(
            snapshot.process_metrics["llm"]["planning"]["input_tokens"],
            10,
        )


if __name__ == "__main__":
    unittest.main()
