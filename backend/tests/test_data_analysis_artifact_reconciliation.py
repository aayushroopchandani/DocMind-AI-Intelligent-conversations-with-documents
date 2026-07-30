from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

from scripts.data_analysis_agent.runtime.bootstrap import AnalysisRuntime
from scripts.data_analysis_agent.runtime.models.artifacts import (
    ArtifactSource,
    ArtifactVersion,
    ArtifactVersionStatus,
    WorkspaceArtifact,
    WorkspaceArtifactType,
)
from scripts.data_analysis_agent.runtime.repositories.artifacts import (
    MongoArtifactRepository,
)
from scripts.data_analysis_agent.runtime.services.artifact_reconciler import (
    ArtifactReconcilerConfig,
    ArtifactUploadReconciler,
)
from scripts.data_analysis_agent.runtime.services.artifacts import (
    ArtifactFinalizationPendingError,
    ArtifactReconciliationSummary,
    ArtifactServiceConfig,
    ArtifactVersionInProgressError,
    ArtifactVersionService,
    CreateArtifactVersion,
)
from scripts.data_analysis_agent.runtime.storage.base import (
    BlobUpload,
    BlobStoreUnavailableError,
)
from tests.test_data_analysis_artifact_storage import (
    InMemoryArtifactRepository,
    InMemoryBlobStore,
)


class _ReconciliationRepository(InMemoryArtifactRepository):
    def __init__(self) -> None:
        super().__init__()
        self.renew_upload_lease_calls = 0

    async def renew_upload_lease(self, **kwargs: Any) -> ArtifactVersion:
        self.renew_upload_lease_calls += 1
        return await super().renew_upload_lease(**kwargs)

    async def list_stale_uploading_versions(
        self,
        *,
        stale_before: datetime,
        current_time: datetime,
        limit: int,
    ) -> tuple[ArtifactVersion, ...]:
        return tuple(
            sorted(
                (
                    version
                    for version in self.versions.values()
                    if version.status == ArtifactVersionStatus.UPLOADING
                    and version.updated_at <= stale_before
                    and (
                        version.upload_lease_expires_at is None
                        or version.upload_lease_expires_at <= current_time
                    )
                ),
                key=lambda item: (item.updated_at, item.version_id),
            )[:limit]
        )

    async def list_ready_versions_with_stale_pointer(
        self,
        *,
        limit: int,
    ) -> tuple[ArtifactVersion, ...]:
        values: list[ArtifactVersion] = []
        for version in self.versions.values():
            if version.status != ArtifactVersionStatus.READY:
                continue
            artifact = self.artifacts.get(
                (
                    version.user_id,
                    version.workspace_id,
                    version.artifact_id,
                )
            )
            if artifact is None:
                continue
            current_number = 0
            if artifact.current_version_id is not None:
                current = self.versions.get(
                    (
                        version.user_id,
                        version.workspace_id,
                        artifact.current_version_id,
                    )
                )
                current_number = current.version_number if current else 0
            if current_number < version.version_number:
                values.append(version)
        return tuple(
            sorted(
                values,
                key=lambda item: (item.updated_at, item.version_id),
            )[:limit]
        )


class _ReconciliationBlobStore(InMemoryBlobStore):
    def __init__(self) -> None:
        super().__init__()
        self.stat_unavailable_once = False

    async def stat(self, reference: Any) -> Any:
        if self.stat_unavailable_once:
            self.stat_unavailable_once = False
            raise BlobStoreUnavailableError("simulated provider outage")
        return await super().stat(reference)


class _SlowUploadBlobStore(_ReconciliationBlobStore):
    def __init__(self) -> None:
        super().__init__()
        self.upload_started = asyncio.Event()
        self.finish_upload = asyncio.Event()

    async def upload(self, upload: BlobUpload) -> Any:
        self.upload_started.set()
        await self.finish_upload.wait()
        return await super().upload(upload)


class _QueryCursor:
    def __init__(self) -> None:
        self.query: dict[str, Any] | None = None
        self.projection: dict[str, int] | None = None
        self.sorting: list[tuple[str, int]] | None = None
        self.bound: int | None = None

    def find(
        self,
        query: dict[str, Any],
        projection: dict[str, int],
    ) -> "_QueryCursor":
        self.query = query
        self.projection = projection
        return self

    def sort(self, sorting: list[tuple[str, int]]) -> "_QueryCursor":
        self.sorting = sorting
        return self

    def limit(self, limit: int) -> "_QueryCursor":
        self.bound = limit
        return self

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        self.bound = length
        return []


class ArtifactReconciliationRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_upload_query_matches_reconciliation_index(self) -> None:
        collection = _QueryCursor()
        cutoff = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
        database = {
            MongoArtifactRepository.versions_collection_name: collection
        }

        with patch(
            "scripts.data_analysis_agent.runtime.repositories.artifacts.get_db",
            return_value=database,
        ):
            values = await MongoArtifactRepository().list_stale_uploading_versions(
                stale_before=cutoff,
                current_time=cutoff,
                limit=17,
            )

        self.assertEqual(values, ())
        self.assertEqual(
            collection.query,
            {
                "status": ArtifactVersionStatus.UPLOADING.value,
                "updated_at": {"$lte": cutoff},
                "$or": [
                    {"upload_owner_id": None},
                    {"upload_owner_id": {"$exists": False}},
                    {"upload_lease_expires_at": None},
                    {"upload_lease_expires_at": {"$lte": cutoff}},
                ],
            },
        )
        self.assertEqual(
            collection.sorting,
            [("updated_at", 1), ("version_id", 1)],
        )
        self.assertEqual(collection.bound, 17)


class ArtifactReconciliationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = _ReconciliationRepository()
        self.store = _ReconciliationBlobStore()
        self.service = ArtifactVersionService(
            repository=self.repository,
            blob_store=self.store,
        )
        self.content = b"region,revenue\nAPAC,51000\n"

    def request(self, *, version_id: str = "version-1") -> CreateArtifactVersion:
        return CreateArtifactVersion(
            user_id="user-1",
            workspace_id="workspace-1",
            artifact_id="artifact-1",
            artifact_type=WorkspaceArtifactType.CSV,
            artifact_name="Revenue data",
            source=ArtifactSource.UPLOADED,
            filename="revenue.csv",
            content_type="text/csv",
            content=self.content,
            version_id=version_id,
        )

    async def reconcile(self) -> ArtifactReconciliationSummary:
        return await self.service.reconcile_stale_versions(
            stale_before=datetime.now(timezone.utc) + timedelta(seconds=1),
            limit=10,
        )

    async def version(self, version_id: str = "version-1") -> ArtifactVersion:
        version = await self.repository.get_version(
            user_id="user-1",
            workspace_id="workspace-1",
            version_id=version_id,
        )
        assert version is not None
        return version

    async def artifact(self) -> WorkspaceArtifact:
        artifact = await self.repository.get_artifact(
            user_id="user-1",
            workspace_id="workspace-1",
            artifact_id="artifact-1",
        )
        assert artifact is not None
        return artifact

    async def test_recorded_blob_is_verified_finalized_and_promoted(self) -> None:
        self.store.fail_verify_unavailable_once = True
        with self.assertRaises(ArtifactFinalizationPendingError):
            await self.service.create_version(self.request())
        pending = await self.version()
        self.assertIsNotNone(pending.blob)

        summary = await self.reconcile()

        ready = await self.version()
        self.assertEqual(summary.finalized, 1)
        self.assertEqual(ready.status, ArtifactVersionStatus.READY)
        self.assertEqual((await self.artifact()).current_version_id, "version-1")
        self.assertEqual(self.store.upload_calls, 1)

    async def test_unrecorded_provider_object_is_adopted_by_exact_identity(
        self,
    ) -> None:
        self.store.accept_then_timeout_once = True
        with self.assertRaises(ArtifactFinalizationPendingError):
            await self.service.create_version(self.request())
        pending = await self.version()
        self.assertIsNone(pending.blob)
        self.assertEqual(len(self.store.objects), 1)

        summary = await self.reconcile()

        ready = await self.version()
        self.assertEqual(summary.finalized, 1)
        self.assertEqual(ready.status, ArtifactVersionStatus.READY)
        self.assertIsNotNone(ready.blob)
        self.assertEqual(self.store.upload_calls, 1)
        self.assertEqual(len(self.store.objects), 1)

    async def test_definitively_missing_unrecorded_object_is_failed(self) -> None:
        self.store.raw_timeout_once = True
        with self.assertRaises(ArtifactFinalizationPendingError):
            await self.service.create_version(self.request())

        summary = await self.reconcile()

        failed = await self.version()
        self.assertEqual(summary.failed, 1)
        self.assertEqual(failed.status, ArtifactVersionStatus.FAILED)
        self.assertEqual(failed.error_code, "artifact_blob_missing")

    async def test_provider_outage_leaves_reservation_pending(self) -> None:
        self.store.raw_timeout_once = True
        with self.assertRaises(ArtifactFinalizationPendingError):
            await self.service.create_version(self.request())
        self.store.stat_unavailable_once = True

        summary = await self.reconcile()

        pending = await self.version()
        self.assertEqual(summary.pending, 1)
        self.assertEqual(pending.status, ArtifactVersionStatus.UPLOADING)
        self.assertIsNone(pending.error_code)

    async def test_uncertain_mismatched_object_is_not_deleted(self) -> None:
        self.store.accept_then_timeout_once = True
        with self.assertRaises(ArtifactFinalizationPendingError):
            await self.service.create_version(self.request())
        object_key = next(iter(self.store.objects))
        self.store.objects[object_key] = b"different bytes"

        summary = await self.reconcile()

        failed = await self.version()
        self.assertEqual(summary.failed, 1)
        self.assertEqual(failed.status, ArtifactVersionStatus.FAILED)
        self.assertEqual(failed.error_code, "artifact_integrity_failed")
        self.assertEqual(self.store.deleted, [])
        self.assertIn(object_key, self.store.objects)

    async def test_ready_version_repairs_stale_parent_pointer(self) -> None:
        created = await self.service.create_version(self.request())
        artifact = await self.artifact()
        key = (artifact.user_id, artifact.workspace_id, artifact.artifact_id)
        self.repository.artifacts[key] = WorkspaceArtifact.model_validate(
            artifact.model_copy(
                update={
                    "current_version_id": None,
                    "updated_at": datetime.now(timezone.utc),
                }
            ).model_dump()
        )

        summary = await self.reconcile()

        self.assertEqual(summary.pointer_repaired, 1)
        self.assertEqual(
            (await self.artifact()).current_version_id,
            created.version.version_id,
        )

    async def test_live_slow_upload_is_fenced_from_stale_reconciliation(
        self,
    ) -> None:
        store = _SlowUploadBlobStore()
        service = ArtifactVersionService(
            repository=self.repository,
            blob_store=store,
            config=ArtifactServiceConfig(
                upload_lease_seconds=10,
                upload_heartbeat_seconds=0.01,
            ),
        )
        upload_task = asyncio.create_task(
            service.create_version(self.request()),
        )
        await asyncio.wait_for(store.upload_started.wait(), timeout=1)
        try:
            live = await self.version()
            self.assertIsNotNone(live.upload_owner_id)
            self.assertIsNotNone(live.upload_lease_expires_at)

            for _ in range(100):
                if self.repository.renew_upload_lease_calls:
                    break
                await asyncio.sleep(0.005)
            self.assertGreaterEqual(
                self.repository.renew_upload_lease_calls,
                1,
            )

            summary = await service.reconcile_stale_versions(
                # Deliberately treat even newly updated rows as "stale": the
                # independent live lease must still exclude this upload.
                stale_before=datetime.now(timezone.utc) + timedelta(days=1),
                limit=10,
            )
            self.assertEqual(summary.inspected, 0)

            direct_result = await service.reconcile_version(live)
            self.assertEqual(direct_result.disposition.value, "pending")
            with self.assertRaises(ArtifactVersionInProgressError):
                await service.create_version(self.request())

            still_uploading = await self.version()
            self.assertEqual(
                still_uploading.status,
                ArtifactVersionStatus.UPLOADING,
            )
            self.assertIsNone(still_uploading.error_code)
        finally:
            store.finish_upload.set()
        result = await asyncio.wait_for(upload_task, timeout=1)

        self.assertEqual(result.version.status, ArtifactVersionStatus.READY)
        self.assertEqual(store.upload_calls, 1)
        self.assertIsNone(result.version.upload_owner_id)
        self.assertIsNone(result.version.upload_lease_expires_at)


class _SweepService:
    def __init__(self) -> None:
        self.calls: list[tuple[datetime, int]] = []
        self.called = asyncio.Event()

    async def reconcile_stale_versions(
        self,
        *,
        stale_before: datetime,
        limit: int,
    ) -> ArtifactReconciliationSummary:
        self.calls.append((stale_before, limit))
        self.called.set()
        return ArtifactReconciliationSummary()


class _LifecycleComponent:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events

    async def start(self) -> None:
        self.events.append(f"{self.name}:start")

    async def stop(self) -> None:
        self.events.append(f"{self.name}:stop")


class ArtifactReconcilerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_periodic_task_runs_bounded_sweep_and_stops_cleanly(self) -> None:
        service = _SweepService()
        now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
        reconciler = ArtifactUploadReconciler(
            service=service,  # type: ignore[arg-type]
            config=ArtifactReconcilerConfig(
                interval_seconds=60,
                stale_after_seconds=120,
                batch_size=7,
            ),
            clock=lambda: now,
        )

        await reconciler.start()
        await asyncio.wait_for(service.called.wait(), timeout=1)
        self.assertTrue(reconciler.is_running)
        await reconciler.stop()

        self.assertFalse(reconciler.is_running)
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(service.calls[0][0], now - timedelta(seconds=120))
        self.assertEqual(service.calls[0][1], 7)

    async def test_analysis_runtime_owns_reconciler_lifecycle(self) -> None:
        events: list[str] = []
        worker = _LifecycleComponent("worker", events)
        reconciler = _LifecycleComponent("reconciler", events)
        runtime = AnalysisRuntime(
            run_service=object(),  # type: ignore[arg-type]
            worker=worker,  # type: ignore[arg-type]
            artifact_service=None,
            artifact_reconciler=reconciler,  # type: ignore[arg-type]
        )

        await runtime.start()
        await runtime.stop()

        self.assertEqual(
            events,
            [
                "reconciler:start",
                "worker:start",
                "worker:stop",
                "reconciler:stop",
            ],
        )


if __name__ == "__main__":
    unittest.main()
