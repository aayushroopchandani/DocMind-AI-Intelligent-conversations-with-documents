"""Phase 9.14.1: reading back what a run executed.

The acceptance criteria these cover:

* a run can be asked what it executed, without the client knowing how an
  execution is addressed internally;
* the response withholds the idempotency internals — execution key, recipe hash,
  input signatures, fencing token, worker, blob keys;
* a cross-tenant read is indistinguishable from a missing one;
* the preview served is the one that was published, bounded and already
  redacted, and an execution with no published result says so rather than
  failing inside the blob store;
* the run points at the execution it published, so the pointer resolves without
  a scan — the worker half of that is pinned in the lifecycle suite.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo import DESCENDING

from apis.analysis_executions import router
from apis.deps import current_user_id, verify_internal_secret
from scripts.data_analysis_agent.runtime.execution.results import (
    MAX_PREVIEW_ROWS,
    ResultPreview,
    build_preview,
)
from scripts.data_analysis_agent.runtime.execution.results.reader import (
    BlobExecutionResultReader,
    ResultUnavailableError,
)
from scripts.data_analysis_agent.runtime.models.artifacts import (
    BlobProvider,
    BlobReference,
)
from scripts.data_analysis_agent.runtime.models.executions import (
    AnalysisExecution,
    CheckpointRecord,
    ExecutionMetrics,
    ExecutionStatus,
    ResultArtifacts,
    StageRecord,
    StageStatus,
)
from scripts.data_analysis_agent.runtime.models.plans import (
    PlanColumn,
    PlanDataType,
)
from scripts.data_analysis_agent.runtime.models.runs import (
    AnalysisMode,
    AnalysisRun,
    AnalysisRunOutcome,
    AnalysisRunPhase,
    AnalysisRunStatus,
)
from scripts.data_analysis_agent.runtime.repositories.executions import (
    ExecutionNotFoundError,
    InMemoryExecutionRepository,
    MongoExecutionRepository,
)
from scripts.data_analysis_agent.runtime.services.execution_reader import (
    ExecutionReadService,
)
from tests.test_data_analysis_phase9_durability import build_service
from tests.test_data_analysis_phase9_execution import _plan, _run_state


_NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)


# --------------------------------------------------------------- fixtures


def _execution(
    *,
    run_id: str,
    user_id: str = "user-1",
    execution_id: str | None = None,
    execution_key: str | None = None,
    created_at: datetime = _NOW,
) -> AnalysisExecution:
    """A reserved execution: enough identity to be found, no result yet."""

    return AnalysisExecution(
        execution_id=execution_id or str(uuid4()),
        execution_key=execution_key or ("a" * 64),
        user_id=user_id,
        workspace_id="workspace-1",
        run_id=run_id,
        plan_id=str(uuid4()),
        plan_hash="b" * 64,
        recipe_hash="c" * 64,
        engine_version="polars-test",
        semantics_version="native-1",
        created_at=created_at,
        updated_at=created_at,
    )


def _run(
    *,
    run_id: str,
    user_id: str = "user-1",
    execution_id: str | None = None,
) -> AnalysisRun:
    values: dict[str, object] = {
        "run_id": run_id,
        "user_id": user_id,
        "workspace_id": "workspace-1",
        "chat_id": "chat-1",
        "idempotency_key": "idempotency-key-1",
        "request_fingerprint": "d" * 64,
        "mode": AnalysisMode.ANALYSE,
        "prompt": "Summarise the selected range.",
        "status": AnalysisRunStatus.SUCCEEDED,
        "phase": AnalysisRunPhase.COMPLETED,
        "outcome": AnalysisRunOutcome.COMPLETED,
        "version": 3,
        "last_event_sequence": 3,
        "created_at": _NOW,
        "updated_at": _NOW,
        "started_at": _NOW,
        "completed_at": _NOW,
    }
    if execution_id:
        values["current_execution_id"] = execution_id
        values["current_execution_key"] = "a" * 64
    return AnalysisRun.model_validate(values)


def _artifacts() -> ResultArtifacts:
    reference = BlobReference(
        provider=BlobProvider.CLOUDINARY,
        object_key="analysis/results/workspace-1/key/result.csv.gz",
        content_type="application/gzip",
        filename="result.csv.gz",
        byte_count=24,
        sha256="e" * 64,
    )
    return ResultArtifacts(
        rows=reference,
        schema_manifest=reference,
        lineage=reference,
        preview=reference,
    )


class _FakeRunService:
    """Only the one method the execution router needs, tenant-scoped."""

    def __init__(self, run: AnalysisRun) -> None:
        self.run = run

    async def get_run(self, *, user_id: str, run_id: str) -> AnalysisRun | None:
        if user_id == self.run.user_id and run_id == self.run.run_id:
            return self.run
        return None


class _StubPreviewReader:
    def __init__(self, preview: dict | None = None) -> None:
        self.preview = preview or {
            "row_count": 2,
            "preview_row_count": 2,
            "truncated": False,
            "privacy_mode": "standard",
            "redacted_column_keys": [],
            "columns": ["region"],
            "rows": [{"region": "north"}, {"region": "south"}],
        }
        self.calls = 0

    async def read_preview(self, execution: AnalysisExecution) -> dict:
        self.calls += 1
        return self.preview


def _client(
    *,
    run: AnalysisRun,
    reader: ExecutionReadService | None,
    user_id: str = "user-1",
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.analysis_run_service = _FakeRunService(run)
    app.state.analysis_execution_reader = reader
    app.dependency_overrides[current_user_id] = lambda: user_id
    app.dependency_overrides[verify_internal_secret] = lambda: None
    return TestClient(app)


# ------------------------------------------------------- preview contract


class PreviewContractTests(unittest.TestCase):
    def test_reader_schema_parses_what_the_builder_writes(self) -> None:
        """The stored-preview schema and its writer must not drift apart."""

        import polars as pl

        columns = (
            PlanColumn(
                key="region",
                label="Region",
                data_type=PlanDataType.STRING,
                nullable=False,
            ),
            PlanColumn(
                key="total",
                label="Total",
                data_type=PlanDataType.INTEGER,
                nullable=False,
            ),
        )
        frame = pl.DataFrame(
            {
                "region": [f"region-{index}" for index in range(50)],
                "total": list(range(50)),
            }
        )

        parsed = ResultPreview.model_validate(build_preview(frame, columns))

        self.assertEqual(parsed.row_count, 50)
        self.assertLessEqual(parsed.preview_row_count, MAX_PREVIEW_ROWS)
        self.assertTrue(parsed.truncated)
        self.assertEqual(parsed.columns, ("region", "total"))
        self.assertEqual(len(parsed.rows), parsed.preview_row_count)

    def test_reader_schema_rejects_an_unbounded_stored_preview(self) -> None:
        """Bounds are re-applied on read; stored bytes are not trusted."""

        with self.assertRaises(ValueError):
            ResultPreview.model_validate(
                {
                    "row_count": 10_000,
                    "preview_row_count": MAX_PREVIEW_ROWS + 1,
                    "privacy_mode": "standard",
                    "columns": ["a"],
                    "rows": [{"a": 1}] * (MAX_PREVIEW_ROWS + 1),
                }
            )

    def test_reader_schema_tolerates_a_newer_publisher(self) -> None:
        """A field this reader does not know must not fail the whole result."""

        parsed = ResultPreview.model_validate(
            {
                "row_count": 1,
                "preview_row_count": 1,
                "privacy_mode": "standard",
                "columns": ["a"],
                "rows": [{"a": 1}],
                "sampling_strategy": "head",
            }
        )
        self.assertEqual(parsed.row_count, 1)


# ------------------------------------------------------ repository lookups


class _FakeExecutionCollection:
    def __init__(self, document: dict | None) -> None:
        self.document = document
        self.queries: list[tuple[dict, object]] = []

    async def find_one(self, query, sort=None):
        self.queries.append((query, sort))
        return self.document


class _FakeExecutionDatabase:
    def __init__(self, collection: _FakeExecutionCollection) -> None:
        self.collection = collection

    def __getitem__(self, _name: str) -> _FakeExecutionCollection:
        return self.collection


class ExecutionLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_newest_attempt_wins_for_a_run(self) -> None:
        repository = InMemoryExecutionRepository()
        run_id = str(uuid4())
        first = await repository.reserve(
            _execution(run_id=run_id, execution_key="1" * 64, created_at=_NOW)
        )
        second = await repository.reserve(
            _execution(
                run_id=run_id,
                execution_key="2" * 64,
                created_at=_NOW + timedelta(minutes=5),
            )
        )

        found = await repository.get_for_run(user_id="user-1", run_id=run_id)

        self.assertEqual(found.execution_id, second.execution_id)
        self.assertNotEqual(found.execution_id, first.execution_id)

    async def test_lookups_are_scoped_to_the_owning_tenant(self) -> None:
        repository = InMemoryExecutionRepository()
        run_id = str(uuid4())
        execution = await repository.reserve(_execution(run_id=run_id))

        self.assertIsNone(
            await repository.get_for_run(user_id="intruder", run_id=run_id)
        )
        self.assertIsNone(
            await repository.get_by_id(
                user_id="intruder",
                execution_id=execution.execution_id,
            )
        )
        self.assertIsNotNone(
            await repository.get_by_id(
                user_id="user-1",
                execution_id=execution.execution_id,
            )
        )

    async def test_unknown_run_has_no_execution(self) -> None:
        repository = InMemoryExecutionRepository()
        self.assertIsNone(
            await repository.get_for_run(user_id="user-1", run_id=str(uuid4()))
        )

    async def test_mongo_lookup_is_tenant_filtered_and_newest_first(self) -> None:
        """Pins the query to the index that backs it."""

        collection = _FakeExecutionCollection(None)
        repository = MongoExecutionRepository(
            _FakeExecutionDatabase(collection)
        )

        await repository.get_for_run(user_id="user-1", run_id="run-1")

        query, sort = collection.queries[0]
        self.assertEqual(query, {"user_id": "user-1", "run_id": "run-1"})
        self.assertEqual(sort, [("created_at", DESCENDING)])


# --------------------------------------------------------- the read service


class ExecutionReadServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemoryExecutionRepository()
        self.run_id = str(uuid4())
        self.service = ExecutionReadService(repository=self.repository)

    async def test_the_runs_own_pointer_is_followed(self) -> None:
        older = await self.repository.reserve(
            _execution(run_id=self.run_id, execution_key="1" * 64)
        )
        await self.repository.reserve(
            _execution(
                run_id=self.run_id,
                execution_key="2" * 64,
                created_at=_NOW + timedelta(minutes=1),
            )
        )

        found = await self.service.get_for_run(
            user_id="user-1",
            run_id=self.run_id,
            execution_id=older.execution_id,
        )

        self.assertEqual(found.execution_id, older.execution_id)

    async def test_a_pointer_into_another_run_is_discarded(self) -> None:
        """A stale pointer must not smuggle a different run's execution out."""

        other = await self.repository.reserve(
            _execution(run_id=str(uuid4()), execution_key="9" * 64)
        )
        mine = await self.repository.reserve(
            _execution(run_id=self.run_id, execution_key="1" * 64)
        )

        found = await self.service.get_for_run(
            user_id="user-1",
            run_id=self.run_id,
            execution_id=other.execution_id,
        )

        self.assertEqual(found.execution_id, mine.execution_id)

    async def test_falls_back_to_the_newest_when_no_pointer_exists(self) -> None:
        newest = await self.repository.reserve(
            _execution(run_id=self.run_id, execution_key="1" * 64)
        )
        found = await self.service.get_for_run(
            user_id="user-1",
            run_id=self.run_id,
        )
        self.assertEqual(found.execution_id, newest.execution_id)

    async def test_a_run_that_never_executed_is_not_found(self) -> None:
        with self.assertRaises(ExecutionNotFoundError):
            await self.service.get_for_run(
                user_id="user-1",
                run_id=self.run_id,
            )

    async def test_preview_needs_a_published_result(self) -> None:
        await self.repository.reserve(_execution(run_id=self.run_id))
        service = ExecutionReadService(
            repository=self.repository,
            preview_reader=_StubPreviewReader(),
        )

        with self.assertRaises(ResultUnavailableError):
            await service.read_preview(user_id="user-1", run_id=self.run_id)

    async def test_preview_needs_configured_storage(self) -> None:
        await self.repository.reserve(_execution(run_id=self.run_id))
        self.assertFalse(self.service.previews_available)

        with self.assertRaises(ResultUnavailableError):
            await self.service.read_preview(
                user_id="user-1",
                run_id=self.run_id,
            )


class PublishedResultReadTests(unittest.IsolatedAsyncioTestCase):
    """Reads against a genuinely published execution, not a hand-built one."""

    async def asyncSetUp(self) -> None:
        self.service, self.store, self.repository = build_service()
        plan = _plan()
        self.outcome = await self.service.execute(
            plan=plan,
            run=_run_state(plan),
        )
        self.assertTrue(self.outcome.succeeded)
        self.plan = plan
        self.reader = ExecutionReadService(
            repository=self.repository,
            preview_reader=BlobExecutionResultReader(self.store),
        )

    async def test_the_published_preview_is_served_as_stored(self) -> None:
        execution, preview = await self.reader.read_preview(
            user_id=self.plan.user_id,
            run_id=self.plan.run_id,
        )

        self.assertEqual(execution.status, ExecutionStatus.SUCCEEDED)
        self.assertIsInstance(preview, ResultPreview)
        parsed = preview
        self.assertEqual(parsed.row_count, execution.metrics.output_rows)
        self.assertLessEqual(parsed.preview_row_count, MAX_PREVIEW_ROWS)
        self.assertEqual(
            parsed.columns,
            tuple(column.key for column in execution.result_columns),
        )

    async def test_the_execution_is_reachable_from_its_run(self) -> None:
        execution = await self.reader.get_for_run(
            user_id=self.plan.user_id,
            run_id=self.plan.run_id,
        )
        self.assertEqual(execution.execution_id, self.outcome.execution_id)
        self.assertIsNotNone(execution.artifacts)


# ------------------------------------------------------------- the HTTP API


class ExecutionAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemoryExecutionRepository()
        self.run_id = str(uuid4())
        self.execution = await self.repository.reserve(
            _execution(run_id=self.run_id)
        )
        self.run = _run(
            run_id=self.run_id,
            execution_id=self.execution.execution_id,
        )
        self.preview_reader = _StubPreviewReader()
        self.reader = ExecutionReadService(
            repository=self.repository,
            preview_reader=self.preview_reader,
        )
        # A second run whose execution really did publish, for the paths that
        # must get past the "has this published?" gate.
        self.published_run_id = str(uuid4())
        published = await self.repository.reserve(
            _execution(run_id=self.published_run_id, execution_key="7" * 64)
        )
        published = await self.repository.start(
            execution=published,
            worker_id="worker-1",
            fencing_token=1,
        )
        await self.repository.publish(
            execution=published,
            content_hash="f" * 64,
            columns=(
                PlanColumn(
                    key="a",
                    label="A",
                    data_type=PlanDataType.INTEGER,
                    nullable=False,
                ),
            ),
            artifacts=_artifacts(),
            metrics=ExecutionMetrics(output_rows=10, output_columns=1),
        )
        self.published_run = _run(
            run_id=self.published_run_id,
            execution_id=published.execution_id,
        )

    def test_the_execution_is_returned_with_its_run(self) -> None:
        with _client(run=self.run, reader=self.reader) as client:
            response = client.get(f"/analysis/runs/{self.run_id}/execution")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["execution"]["execution_id"],
            self.execution.execution_id,
        )
        self.assertEqual(body["execution"]["status"], "reserved")
        self.assertEqual(body["execution"]["has_result"], False)
        self.assertEqual(body["run"]["run_id"], self.run_id)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_idempotency_internals_are_never_serialized(self) -> None:
        """The key, its inputs and the blob keys stay server-side."""

        with _client(run=self.run, reader=self.reader) as client:
            response = client.get(f"/analysis/runs/{self.run_id}/execution")

        execution = response.json()["execution"]
        for field in (
            "execution_key",
            "recipe_hash",
            "input_signatures",
            "fencing_token",
            "worker_id",
            "artifacts",
            "user_id",
            "workspace_id",
        ):
            self.assertNotIn(field, execution)

    async def test_stage_checkpoints_are_never_serialized(self) -> None:
        """A checkpoint is how a worker resumes; a client has no use for it."""

        stage = StageRecord(
            stage_id="stage-1",
            step_ids=("step-1",),
            status=StageStatus.COMPLETED,
            input_rows=10,
            output_rows=4,
            output_columns=2,
            duration_ms=12.5,
            checkpoint=CheckpointRecord(
                stage_id="stage-1",
                stage_recipe_hash="1" * 64,
                content_hash="2" * 64,
                engine_version="polars-test",
                semantics_version="native-1",
                row_count=4,
            ),
        )
        staged = await self.repository.record_stage(
            execution=self.execution,
            stage=stage,
        )

        with _client(run=self.run, reader=self.reader) as client:
            response = client.get(f"/analysis/runs/{self.run_id}/execution")

        execution = response.json()["execution"]
        self.assertEqual(execution["current_stage_id"], "stage-1")
        self.assertEqual(len(execution["stages"]), 1)
        self.assertEqual(execution["stages"][0]["output_rows"], 4)
        self.assertNotIn("checkpoint", execution["stages"][0])
        # The record really did hold one; the view is what dropped it.
        self.assertIsNotNone(staged.stages[0].checkpoint)

    def test_another_tenant_cannot_read_the_execution(self) -> None:
        with _client(
            run=self.run,
            reader=self.reader,
            user_id="intruder",
        ) as client:
            response = client.get(f"/analysis/runs/{self.run_id}/execution")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Analysis execution not found")

    def test_an_unknown_run_is_not_found(self) -> None:
        with _client(run=self.run, reader=self.reader) as client:
            response = client.get(f"/analysis/runs/{uuid4()}/execution")

        self.assertEqual(response.status_code, 404)

    def test_a_run_without_an_execution_is_not_found(self) -> None:
        empty = ExecutionReadService(repository=InMemoryExecutionRepository())
        with _client(run=self.run, reader=empty) as client:
            response = client.get(f"/analysis/runs/{self.run_id}/execution")

        self.assertEqual(response.status_code, 404)

    def test_a_malformed_run_id_is_rejected_before_any_lookup(self) -> None:
        with _client(run=self.run, reader=self.reader) as client:
            response = client.get("/analysis/runs/not-a-uuid/execution")

        self.assertEqual(response.status_code, 422)

    def test_preview_conflicts_while_no_result_is_published(self) -> None:
        with _client(run=self.run, reader=self.reader) as client:
            response = client.get(
                f"/analysis/runs/{self.run_id}/execution/preview"
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.preview_reader.calls, 0)

    async def test_a_failed_execution_reports_its_typed_failure(self) -> None:
        """The UI needs to say why, not just that the run stopped."""

        failed = await self.repository.fail(
            execution=self.execution,
            code="input_unavailable",
            message="dataset version no longer exists",
        )

        with _client(run=self.run, reader=self.reader) as client:
            response = client.get(f"/analysis/runs/{self.run_id}/execution")

        execution = response.json()["execution"]
        self.assertEqual(execution["status"], failed.status.value)
        self.assertEqual(execution["failure_code"], "input_unavailable")
        self.assertIn("no longer exists", execution["failure_message"])
        self.assertFalse(execution["has_result"])

    def test_an_unreadable_stored_preview_is_a_conflict_not_a_crash(self) -> None:
        """A stored document that broke its own contract must not 500."""

        unbounded = _StubPreviewReader(
            {
                "row_count": 10,
                "preview_row_count": MAX_PREVIEW_ROWS + 5,
                "privacy_mode": "standard",
                "columns": ["a"],
                "rows": [{"a": 1}] * (MAX_PREVIEW_ROWS + 5),
            }
        )
        reader = ExecutionReadService(
            repository=self.repository,
            preview_reader=unbounded,
        )

        with _client(run=self.published_run, reader=reader) as client:
            response = client.get(
                f"/analysis/runs/{self.published_run.run_id}/execution/preview"
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("preview contract", response.json()["detail"])

    def test_a_deployment_without_the_reader_is_unavailable(self) -> None:
        with _client(run=self.run, reader=None) as client:
            response = client.get(f"/analysis/runs/{self.run_id}/execution")

        self.assertEqual(response.status_code, 503)


class ExecutionPreviewAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.service, self.store, self.repository = build_service()
        self.plan = _plan()
        outcome = await self.service.execute(
            plan=self.plan,
            run=_run_state(self.plan),
        )
        self.assertTrue(outcome.succeeded)
        self.outcome = outcome
        self.run = AnalysisRun.model_validate(
            {
                **_run(run_id=self.plan.run_id).model_dump(),
                "user_id": self.plan.user_id,
                "workspace_id": self.plan.workspace_id,
                "current_execution_id": outcome.execution_id,
                "current_execution_key": outcome.execution_key,
            }
        )
        self.reader = ExecutionReadService(
            repository=self.repository,
            preview_reader=BlobExecutionResultReader(self.store),
        )

    def test_the_preview_is_bound_to_the_result_it_samples(self) -> None:
        with _client(
            run=self.run,
            reader=self.reader,
            user_id=self.plan.user_id,
        ) as client:
            response = client.get(
                f"/analysis/runs/{self.plan.run_id}/execution/preview"
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["execution_id"], self.outcome.execution_id)
        self.assertEqual(body["content_hash"], self.outcome.content_hash)
        self.assertLessEqual(
            body["preview"]["preview_row_count"],
            MAX_PREVIEW_ROWS,
        )

    def test_the_metadata_route_never_touches_blob_storage(self) -> None:
        """A client polling a run must not cost a download per poll."""

        before = dict(self.store.objects)
        with _client(
            run=self.run,
            reader=self.reader,
            user_id=self.plan.user_id,
        ) as client:
            response = client.get(
                f"/analysis/runs/{self.plan.run_id}/execution"
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["execution"]["has_result"])
        self.assertEqual(self.store.objects, before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
