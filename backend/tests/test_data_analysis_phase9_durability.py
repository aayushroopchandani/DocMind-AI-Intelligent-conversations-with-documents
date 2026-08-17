"""Phase 9.8 durable orchestration and Phase 9.9 result publication.

The acceptance criteria these cover:

9.8 — duplicate queue delivery does not duplicate outputs; stale workers cannot
overwrite a newer attempt; a crash between upload and commit is recoverable.

9.9 — the CSV/schema pair round-trips every logical type; large output rows
never enter MongoDB; a corrupt stored asset is never marked ready; replaying a
recipe produces the same canonical content hash.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from datetime import date

import polars as pl

from scripts.data_analysis_agent.runtime.execution import NativeExecutionService
from scripts.data_analysis_agent.runtime.execution.checkpoints import (
    CHECKPOINT_ROW_THRESHOLD,
    decide,
    plan_checkpoints,
    stage_recipe_hash,
)
from scripts.data_analysis_agent.runtime.execution.contracts import (
    ExecutionFailureCode,
)
from scripts.data_analysis_agent.runtime.execution.publication import ResultPublisher
from scripts.data_analysis_agent.runtime.execution.results import (
    NULL_SENTINEL,
    build_preview,
    build_schema_manifest,
    decode_rows,
    encode_rows,
)
from scripts.data_analysis_agent.runtime.models.artifacts import (
    BlobProvider,
    BlobReference,
)
from scripts.data_analysis_agent.runtime.models.executions import (
    AnalysisExecution,
    ExecutionStatus,
)
from scripts.data_analysis_agent.runtime.models.plans import (
    PlanColumn,
    PlanDataType,
)
from scripts.data_analysis_agent.runtime.repositories.executions import (
    ExecutionFencedError,
    InMemoryExecutionRepository,
)

from tests.test_data_analysis_phase9_execution import (
    ENGINE_READY,
    _StubResolver,
    _plan,
    _run_state,
)


def column(key: str, data_type: PlanDataType, *, unit=None, nullable=True):
    return PlanColumn(
        key=key,
        label=key.replace("_", " ").title(),
        data_type=data_type,
        unit=unit,
        nullable=nullable,
    )


class RecordingBlobStore:
    """A blob store that keeps bytes in memory and can be told to corrupt one."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.upload_count = 0
        self.corrupt_member: str | None = None

    async def upload(self, upload):
        self.upload_count += 1
        self.objects[upload.object_key] = upload.content
        digest = upload.sha256
        if self.corrupt_member and upload.filename == self.corrupt_member:
            # The provider claims a different digest than the bytes we sent.
            digest = "f" * 64
        return BlobReference(
            provider=BlobProvider.CLOUDINARY,
            object_key=upload.object_key,
            content_type=upload.content_type,
            filename=upload.filename,
            byte_count=len(upload.content),
            sha256=digest,
        )

    async def verify_checksum(self, reference: BlobReference) -> None:
        content = self.objects.get(reference.object_key)
        if content is None:
            raise RuntimeError("stored object is missing")
        if hashlib.sha256(content).hexdigest() != reference.sha256:
            raise RuntimeError("stored object does not match its checksum")

    async def download(self, reference: BlobReference, **_kwargs) -> bytes:
        return self.objects[reference.object_key]

    async def stat(self, reference): ...

    async def generate_signed_download(self, *args, **kwargs): ...

    async def signed_download_url(self, *args, **kwargs): ...

    async def delete(self, reference) -> bool:
        return self.objects.pop(reference.object_key, None) is not None


def build_service(store=None, repository=None, rows: int = 8):
    store = store or RecordingBlobStore()
    repository = repository or InMemoryExecutionRepository()
    service = NativeExecutionService(
        resolver=_StubResolver(rows_per_dataset=rows),
        publisher=ResultPublisher(repository=repository, store=store),
        capabilities=ENGINE_READY,
    )
    return service, store, repository


# --------------------------------------------------------------- 9.9 format


class SerializationTests(unittest.TestCase):
    columns = (
        column("text", PlanDataType.STRING),
        column("count", PlanDataType.INTEGER),
        column("amount", PlanDataType.CURRENCY, unit="USD"),
        column("captured_on", PlanDataType.DATE),
        column("flagged", PlanDataType.BOOLEAN),
    )
    rows = [
        {
            "text": "plain",
            "count": 1,
            "amount": 1.005,
            "captured_on": date(2026, 1, 31),
            "flagged": True,
        },
        # An empty string and a null must survive as different values.
        {
            "text": "",
            "count": 0,
            "amount": 0.0,
            "captured_on": None,
            "flagged": False,
        },
        # A literal that collides with the null sentinel.
        {
            "text": NULL_SENTINEL,
            "count": -5,
            "amount": -2.5,
            "captured_on": date(2026, 2, 1),
            "flagged": None,
        },
        {
            "text": 'quotes " commas , and\nnewlines',
            "count": None,
            "amount": None,
            "captured_on": None,
            "flagged": True,
        },
    ]

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.rows,
            schema={
                "text": pl.String,
                "count": pl.Int64,
                "amount": pl.Float64,
                "captured_on": pl.Date,
                "flagged": pl.Boolean,
            },
        )

    def test_every_logical_type_round_trips(self) -> None:
        payload = encode_rows(self.frame(), self.columns)

        restored = decode_rows(payload, self.columns)

        self.assertEqual(restored, self.rows)

    def test_an_empty_string_stays_distinct_from_null(self) -> None:
        restored = decode_rows(encode_rows(self.frame(), self.columns), self.columns)

        self.assertEqual(restored[1]["text"], "")
        self.assertIsNone(restored[3]["count"])

    def test_the_null_sentinel_can_appear_as_real_text(self) -> None:
        restored = decode_rows(encode_rows(self.frame(), self.columns), self.columns)

        self.assertEqual(restored[2]["text"], NULL_SENTINEL)

    def test_identical_input_produces_identical_bytes(self) -> None:
        # Replay must be byte-identical, not merely semantically equal.
        self.assertEqual(
            encode_rows(self.frame(), self.columns),
            encode_rows(self.frame(), self.columns),
        )

    def test_the_manifest_describes_what_csv_cannot(self) -> None:
        manifest = build_schema_manifest(
            self.columns,
            row_count=4,
            content_hash="a" * 64,
        )

        self.assertEqual(manifest["encoding"]["null_sentinel"], NULL_SENTINEL)
        self.assertFalse(
            manifest["encoding"]["empty_string_is_not_null"] is None
        )
        self.assertEqual(manifest["semantics"]["timezone"], "UTC")
        amount = next(
            item for item in manifest["columns"] if item["key"] == "amount"
        )
        self.assertEqual(amount["data_type"], "currency")
        self.assertEqual(amount["unit"], "USD")


class PreviewTests(unittest.TestCase):
    def test_a_preview_is_bounded_and_marks_truncation(self) -> None:
        columns = (column("value", PlanDataType.INTEGER),)
        frame = pl.DataFrame({"value": list(range(500))})

        preview = build_preview(frame, columns)

        self.assertEqual(preview["row_count"], 500)
        self.assertLessEqual(preview["preview_row_count"], 20)
        self.assertTrue(preview["truncated"])

    def test_preview_text_cannot_become_a_live_formula(self) -> None:
        columns = (column("label", PlanDataType.STRING),)
        frame = pl.DataFrame({"label": ["=cmd|' /c calc'!A1"]})

        preview = build_preview(frame, columns)

        self.assertTrue(preview["rows"][0]["label"].startswith("'"))


# ------------------------------------------------------- 9.8 durable records


class ExecutionRecordTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_successful_run_publishes_a_durable_record(self) -> None:
        service, store, repository = build_service()
        plan = _plan()

        outcome = await service.execute(plan=plan, run=_run_state(plan))

        self.assertTrue(outcome.succeeded, outcome.failure_message)
        self.assertTrue(outcome.published)
        record = await repository.get_by_key(
            user_id=plan.user_id,
            execution_key=outcome.execution_key,
        )
        self.assertEqual(record.status, ExecutionStatus.SUCCEEDED)
        self.assertEqual(record.result_content_hash, outcome.content_hash)
        self.assertIsNotNone(record.finished_at)

    async def test_the_bundle_has_all_four_members(self) -> None:
        service, store, _repository = build_service()
        plan = _plan()

        await service.execute(plan=plan, run=_run_state(plan))

        names = sorted(key.rsplit("/", 1)[-1] for key in store.objects)
        self.assertEqual(
            names,
            [
                "result.csv.gz",
                "result.lineage.json",
                "result.preview.json",
                "result.schema.json",
            ],
        )

    async def test_duplicate_delivery_produces_one_execution(self) -> None:
        service, store, repository = build_service()
        plan = _plan()

        first = await service.execute(plan=plan, run=_run_state(plan))
        second = await service.execute(plan=plan, run=_run_state(plan))

        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.execution_id, second.execution_id)
        # Four uploads total, not eight.
        self.assertEqual(store.upload_count, 4)

    async def test_the_record_holds_pointers_not_rows(self) -> None:
        service, _store, repository = build_service(rows=50)
        plan = _plan()

        outcome = await service.execute(plan=plan, run=_run_state(plan))
        record = await repository.get_by_key(
            user_id=plan.user_id,
            execution_key=outcome.execution_key,
        )

        # 9.9.4: MongoDB stores identity, hashes, bounded metrics and
        # references — never the table itself.
        document = record.model_dump(mode="json")
        serialized = json.dumps(document)
        self.assertNotIn("value-0", serialized)
        self.assertIn("object_key", serialized)
        self.assertEqual(record.metrics.output_rows, 50)

    async def test_a_stale_worker_cannot_publish_over_a_newer_attempt(self) -> None:
        service, _store, repository = build_service()
        plan = _plan()

        # Attempt 2 claims the record first.
        newer = await service.execute(
            plan=plan,
            run=_run_state(plan),
            fencing_token=2,
        )
        self.assertTrue(newer.succeeded, newer.failure_message)

        record = await repository.get_by_key(
            user_id=plan.user_id,
            execution_key=newer.execution_key,
        )
        stale = record.model_copy(update={"fencing_token": 1, "version": 1})

        with self.assertRaises(ExecutionFencedError):
            await repository.start(
                execution=stale,
                worker_id="stale-worker",
                fencing_token=1,
            )

    async def test_a_stale_worker_cannot_commit_its_result(self) -> None:
        service, _store, repository = build_service()
        plan = _plan()

        outcome = await service.execute(
            plan=plan,
            run=_run_state(plan),
            fencing_token=2,
        )
        record = await repository.get_by_key(
            user_id=plan.user_id,
            execution_key=outcome.execution_key,
        )
        # A worker still holding the pre-publish snapshot tries to commit.
        stale = record.model_copy(update={"version": record.version - 1})

        with self.assertRaises(ExecutionFencedError):
            await repository.publish(
                execution=stale,
                content_hash="e" * 64,
                columns=record.result_columns,
                artifacts=record.artifacts,
                metrics=record.metrics,
            )

        current = await repository.get_by_key(
            user_id=plan.user_id,
            execution_key=outcome.execution_key,
        )
        self.assertEqual(current.result_content_hash, outcome.content_hash)

    async def test_a_crash_before_commit_leaves_a_recoverable_record(self) -> None:
        """Objects without a record is the recoverable failure mode.

        The bundle is uploaded before the commit, so this simulates the crash
        window: the objects exist, the record has not succeeded, and nothing
        claims a result that was never stored.
        """

        store = RecordingBlobStore()
        repository = InMemoryExecutionRepository()
        service, _store, _repository = build_service(
            store=store,
            repository=repository,
        )
        plan = _plan()

        published: list[str] = []
        original = repository.publish

        async def crash_before_commit(**kwargs):
            published.append("attempted")
            raise ExecutionFencedError("simulated crash before commit")

        repository.publish = crash_before_commit  # type: ignore[method-assign]
        outcome = await service.execute(plan=plan, run=_run_state(plan))
        repository.publish = original  # type: ignore[method-assign]

        self.assertTrue(published)
        self.assertFalse(outcome.succeeded)
        # The bundle is durable; the record never claimed success.
        self.assertEqual(store.upload_count, 4)
        record = await repository.get_by_key(
            user_id=plan.user_id,
            execution_key=outcome.execution_key,
        )
        self.assertNotEqual(record.status, ExecutionStatus.SUCCEEDED)
        self.assertIsNone(record.artifacts)

    async def test_a_corrupt_stored_asset_is_never_marked_ready(self) -> None:
        store = RecordingBlobStore()
        store.corrupt_member = "result.csv.gz"
        service, _store, repository = build_service(store=store)
        plan = _plan()

        outcome = await service.execute(plan=plan, run=_run_state(plan))

        self.assertFalse(outcome.succeeded)
        record = await repository.get_by_key(
            user_id=plan.user_id,
            execution_key=outcome.execution_key,
        )
        self.assertEqual(record.status, ExecutionStatus.FAILED)
        self.assertIsNone(record.artifacts)

    async def test_the_stored_rows_decode_back_to_the_result(self) -> None:
        service, store, repository = build_service(rows=5)
        plan = _plan()

        outcome = await service.execute(plan=plan, run=_run_state(plan))
        record = await repository.get_by_key(
            user_id=plan.user_id,
            execution_key=outcome.execution_key,
        )
        payload = store.objects[record.artifacts.rows.object_key]

        restored = decode_rows(payload, record.result_columns)

        self.assertEqual(len(restored), 5)
        self.assertEqual(
            list(restored[0]),
            [column.key for column in record.result_columns],
        )

    async def test_the_lineage_document_can_replay_the_recipe(self) -> None:
        service, store, repository = build_service()
        plan = _plan()

        outcome = await service.execute(plan=plan, run=_run_state(plan))
        record = await repository.get_by_key(
            user_id=plan.user_id,
            execution_key=outcome.execution_key,
        )
        lineage = json.loads(
            store.objects[record.artifacts.lineage.object_key].decode("utf-8")
        )

        self.assertEqual(lineage["identity"]["plan_hash"], plan.plan_hash)
        self.assertEqual(lineage["output"]["content_hash"], outcome.content_hash)
        self.assertEqual(lineage["replay"]["recipe_hash"], record.recipe_hash)
        self.assertTrue(lineage["replay"]["steps"])

    async def test_the_preview_member_is_bounded(self) -> None:
        service, store, repository = build_service(rows=200)
        plan = _plan()

        outcome = await service.execute(plan=plan, run=_run_state(plan))
        record = await repository.get_by_key(
            user_id=plan.user_id,
            execution_key=outcome.execution_key,
        )
        preview = json.loads(
            store.objects[record.artifacts.preview.object_key].decode("utf-8")
        )

        self.assertEqual(preview["row_count"], 200)
        self.assertLessEqual(preview["preview_row_count"], 20)

    async def test_a_failed_execution_records_its_typed_failure(self) -> None:
        service, _store, repository = build_service()
        plan = _plan()

        outcome = await service.execute(
            plan=plan,
            run=_run_state(plan, cancellation_requested=True),
        )

        # Admission rejected it before a record was ever reserved.
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.failure_code, ExecutionFailureCode.CANCELLED)
        self.assertIsNone(
            await repository.get_by_key(
                user_id=plan.user_id,
                execution_key=outcome.execution_key,
            )
        )


class ExecutionRecordModelTests(unittest.TestCase):
    def _record(self, **overrides) -> dict:
        values = {
            "execution_id": "exec-1",
            "execution_key": "a" * 64,
            "user_id": "user-1",
            "workspace_id": "workspace-1",
            "run_id": "0" * 36,
            "plan_id": "1" * 36,
            "plan_hash": "b" * 64,
            "recipe_hash": "c" * 64,
            "engine_version": "polars-1.43.2",
            "semantics_version": "2.0",
        }
        values.update(overrides)
        return values

    def test_a_terminal_status_requires_a_finish_time(self) -> None:
        with self.assertRaises(ValueError):
            AnalysisExecution.model_validate(
                self._record(status=ExecutionStatus.SUCCEEDED)
            )

    def test_a_succeeded_record_requires_a_published_result(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            AnalysisExecution.model_validate(
                self._record(
                    status=ExecutionStatus.SUCCEEDED,
                    started_at=now,
                    finished_at=now,
                )
            )

    def test_a_reserved_record_is_valid_on_its_own(self) -> None:
        record = AnalysisExecution.model_validate(self._record())

        self.assertEqual(record.status, ExecutionStatus.RESERVED)
        self.assertFalse(record.is_terminal)


# ---------------------------------------------------------- 9.8 checkpoints


class CheckpointPolicyTests(unittest.TestCase):
    def _step(self, kind: str, output_alias: str = "out"):
        from scripts.data_analysis_agent.runtime.models.plans import (
            PlanStepEstimate,
            SelectColumnsStep,
            StepProvenance,
        )

        return SelectColumnsStep(
            step_id=f"{kind}_step",
            executor="native",
            input_alias="src",
            output_alias=output_alias,
            column_keys=("a",),
            expected_schema=(column("a", PlanDataType.STRING),),
            estimate=PlanStepEstimate(),
            provenance=StepProvenance(description="probe"),
        ).model_copy(update={"kind": kind})

    def test_the_final_result_is_always_checkpointed(self) -> None:
        decision = decide(
            self._step("select_columns"),
            output_rows=1,
            consumers=0,
            is_final=True,
        )

        self.assertTrue(decision.should_store)
        self.assertEqual(decision.reason, "final_result")

    def test_a_small_filter_is_not_worth_storing(self) -> None:
        decision = decide(
            self._step("filter_rows"),
            output_rows=10,
            consumers=1,
            is_final=False,
        )

        self.assertFalse(decision.should_store)

    def test_a_fan_out_branch_is_stored_once(self) -> None:
        decision = decide(
            self._step("filter_rows"),
            output_rows=10,
            consumers=3,
            is_final=False,
        )

        self.assertTrue(decision.should_store)
        self.assertEqual(decision.reason, "fan_out_branch")

    def test_a_materialization_barrier_is_stored(self) -> None:
        decision = decide(
            self._step("pivot"),
            output_rows=5,
            consumers=1,
            is_final=False,
        )

        self.assertTrue(decision.should_store)
        self.assertEqual(decision.reason, "materialization_barrier")

    def test_a_large_join_is_stored_but_a_small_one_is_not(self) -> None:
        large = decide(
            self._step("join"),
            output_rows=CHECKPOINT_ROW_THRESHOLD,
            consumers=1,
            is_final=False,
        )
        small = decide(
            self._step("join"),
            output_rows=10,
            consumers=1,
            is_final=False,
        )

        self.assertTrue(large.should_store)
        self.assertFalse(small.should_store)

    def test_a_stage_hash_changes_with_its_steps(self) -> None:
        first = self._step("filter_rows", output_alias="one")
        second = self._step("filter_rows", output_alias="two")

        self.assertNotEqual(
            stage_recipe_hash((first,)),
            stage_recipe_hash((second,)),
        )
        self.assertEqual(
            stage_recipe_hash((first,)),
            stage_recipe_hash((first,)),
        )

    def test_every_step_gets_a_decision(self) -> None:
        steps = (
            self._step("filter_rows", output_alias="filtered"),
            self._step("select_columns", output_alias="final"),
        )

        decisions = plan_checkpoints(
            steps,
            result_alias="final",
            row_counts={"filtered": 10, "final": 10},
        )

        self.assertEqual(set(decisions), {step.step_id for step in steps})


class CheckpointReuseTests(unittest.TestCase):
    def _checkpoint(self, **overrides):
        from scripts.data_analysis_agent.runtime.models.executions import (
            CheckpointRecord,
        )

        values = {
            "stage_id": "stage-1",
            "stage_recipe_hash": "a" * 64,
            "input_signatures": ("b" * 64,),
            "content_hash": "c" * 64,
            "engine_version": "polars-1.43.2",
            "semantics_version": "2.0",
            "row_count": 10,
            "blob": BlobReference(
                provider=BlobProvider.CLOUDINARY,
                object_key="k",
                content_type="application/gzip",
                filename="stage.csv.gz",
                byte_count=1,
                sha256="d" * 64,
            ),
        }
        values.update(overrides)
        return CheckpointRecord.model_validate(values)

    def test_a_matching_checkpoint_is_reusable(self) -> None:
        self.assertTrue(
            self._checkpoint().reusable_for(
                stage_recipe_hash="a" * 64,
                input_signatures=("b" * 64,),
                engine_version="polars-1.43.2",
                semantics_version="2.0",
            )
        )

    def test_any_drift_makes_a_checkpoint_unusable(self) -> None:
        checkpoint = self._checkpoint()
        base = {
            "stage_recipe_hash": "a" * 64,
            "input_signatures": ("b" * 64,),
            "engine_version": "polars-1.43.2",
            "semantics_version": "2.0",
        }

        for field, value in (
            ("stage_recipe_hash", "e" * 64),
            ("input_signatures", ("f" * 64,)),
            ("engine_version", "polars-9.9"),
            ("semantics_version", "3.0"),
        ):
            with self.subTest(field=field):
                self.assertFalse(checkpoint.reusable_for(**{**base, field: value}))

    def test_a_checkpoint_without_stored_bytes_is_not_reusable(self) -> None:
        self.assertFalse(
            self._checkpoint(blob=None).reusable_for(
                stage_recipe_hash="a" * 64,
                input_signatures=("b" * 64,),
                engine_version="polars-1.43.2",
                semantics_version="2.0",
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
