from __future__ import annotations

import unittest
from typing import Any

from db.indexes.analysis import (
    ANALYSIS_INDEX_DEFINITIONS,
    DEPRECATED_ANALYSIS_INDEXES,
    LEGACY_ANALYSIS_INDEX_REPLACEMENTS,
    ensure_analysis_indexes,
    migrate_analysis_indexes,
    verify_analysis_indexes,
)


class _FakeCollection:
    def __init__(self) -> None:
        self.index_batches: list[list[Any]] = []
        self.installed: list[dict[str, Any]] = [
            {"name": "_id_", "key": {"_id": 1}}
        ]

    async def create_indexes(self, indexes: list[Any]) -> list[str]:
        self.index_batches.append(indexes)
        self.installed.extend(dict(index.document) for index in indexes)
        return [str(index.document["name"]) for index in indexes]

    def list_indexes(self) -> Any:
        async def iterator() -> Any:
            for document in self.installed:
                yield document

        return iterator()

    async def drop_index(self, name: str) -> None:
        before = len(self.installed)
        self.installed = [
            document
            for document in self.installed
            if document.get("name") != name
        ]
        if len(self.installed) == before:
            raise AssertionError(f"index was not installed: {name}")


class _FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())


def _definitions_by_collection() -> dict[str, tuple[Any, ...]]:
    return {
        group.collection_name: group.indexes
        for group in ANALYSIS_INDEX_DEFINITIONS
    }


def _definition_by_name(collection_name: str, index_name: str) -> Any:
    return next(
        definition
        for definition in _definitions_by_collection()[collection_name]
        if definition.name == index_name
    )


class AnalysisIndexDefinitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_installer_batches_one_create_command_per_collection(self) -> None:
        database = _FakeDatabase()

        await ensure_analysis_indexes(database)

        self.assertEqual(
            set(database.collections),
            {
                "analysis_runs",
                "analysis_run_events",
                "workspace_artifacts",
                "artifact_versions",
                "dataset_catalog",
                "analysis_plans",
                "analysis_executions",
                "analysis_patch_proposals",
                "analysis_write_reservations",
            },
        )
        for group in ANALYSIS_INDEX_DEFINITIONS:
            batches = database.collections[group.collection_name].index_batches
            self.assertEqual(len(batches), 1)
            self.assertEqual(len(batches[0]), len(group.indexes))

    async def test_identity_and_ordering_constraints_match_repository_queries(
        self,
    ) -> None:
        expected_unique_keys = {
            ("analysis_runs", "uq_analysis_runs_user_run"): (
                ("user_id", 1),
                ("run_id", 1),
            ),
            ("analysis_runs", "uq_analysis_runs_user_idempotency"): (
                ("user_id", 1),
                ("idempotency_key", 1),
            ),
            (
                "analysis_run_events",
                "uq_analysis_run_events_sequence",
            ): (
                ("user_id", 1),
                ("run_id", 1),
                ("sequence", 1),
            ),
            ("workspace_artifacts", "uq_workspace_artifacts_identity"): (
                ("user_id", 1),
                ("workspace_id", 1),
                ("artifact_id", 1),
            ),
            ("artifact_versions", "uq_artifact_versions_identity"): (
                ("user_id", 1),
                ("workspace_id", 1),
                ("version_id", 1),
            ),
            ("artifact_versions", "uq_artifact_versions_number"): (
                ("user_id", 1),
                ("workspace_id", 1),
                ("artifact_id", 1),
                ("version_number", -1),
            ),
            ("dataset_catalog", "uq_dataset_catalog_version"): (
                ("user_id", 1),
                ("workspace_id", 1),
                ("dataset_id", 1),
                ("source_version", 1),
            ),
            ("analysis_plans", "uq_analysis_plans_run_revision"): (
                ("user_id", 1),
                ("run_id", 1),
                ("revision", 1),
            ),
            ("analysis_plans", "uq_analysis_plans_identity"): (
                ("user_id", 1),
                ("run_id", 1),
                ("plan_id", 1),
            ),
            ("analysis_plans", "uq_analysis_plans_run_hash"): (
                ("user_id", 1),
                ("run_id", 1),
                ("plan_hash", 1),
            ),
            (
                "analysis_patch_proposals",
                "uq_analysis_patch_proposals_identity",
            ): (
                ("user_id", 1),
                ("run_id", 1),
                ("patch_id", 1),
                # A rebase issues a new revision of the same patch (9.12.1),
                # so identity is the pair rather than the patch alone.
                ("revision", 1),
            ),
            (
                "analysis_write_reservations",
                "uq_analysis_write_reservations_identity",
            ): (
                ("user_id", 1),
                ("reservation_id", 1),
            ),
            (
                "analysis_write_reservations",
                "uq_analysis_write_reservations_patch_revision",
            ): (
                ("user_id", 1),
                ("run_id", 1),
                ("patch_id", 1),
                ("patch_revision", 1),
            ),
        }

        for (collection_name, index_name), expected_keys in (
            expected_unique_keys.items()
        ):
            definition = _definition_by_name(collection_name, index_name)
            self.assertTrue(definition.unique)
            self.assertEqual(definition.keys, expected_keys)

    async def test_optional_keys_use_safe_partial_unique_indexes(self) -> None:
        idempotency = _definition_by_name(
            "analysis_runs",
            "uq_analysis_runs_user_idempotency",
        )
        event_deduplication = _definition_by_name(
            "analysis_run_events",
            "uq_analysis_run_events_deduplication",
        )

        self.assertEqual(
            dict(idempotency.partial_filter or {}),
            {"idempotency_key": {"$type": "string"}},
        )
        self.assertEqual(
            dict(event_deduplication.partial_filter or {}),
            {"deduplication_key": {"$type": "string"}},
        )
        self.assertTrue(event_deduplication.unique)

        plan_reservation = _definition_by_name(
            "analysis_plans",
            "uq_analysis_plans_active_write_reservations",
        )
        self.assertTrue(plan_reservation.unique)
        self.assertEqual(
            dict(plan_reservation.partial_filter or {}),
            {"reservation_active": True},
        )

    async def test_verifier_reports_no_drift_after_install(self) -> None:
        database = _FakeDatabase()

        await ensure_analysis_indexes(database)
        report = await verify_analysis_indexes(database)

        self.assertTrue(report.ok)
        self.assertEqual(report.drift, ())

    async def test_verifier_detects_missing_index_and_unexpected_ttl(self) -> None:
        database = _FakeDatabase()
        await ensure_analysis_indexes(database)
        plans = database["analysis_plans"]
        plans.installed = [
            document
            for document in plans.installed
            if document.get("name") != "uq_analysis_plans_run_hash"
        ]
        plans.installed.append(
            {
                "name": "legacy_ttl",
                "key": {"updated_at": 1},
                "expireAfterSeconds": 60,
            }
        )
        runs = database["analysis_runs"]
        for document in runs.installed:
            if document.get("name") == "uq_analysis_runs_user_run":
                document["expireAfterSeconds"] = 3600

        report = await verify_analysis_indexes(database)

        reasons = {
            (item.collection_name, item.index_name, item.reason)
            for item in report.drift
        }
        self.assertIn(
            ("analysis_plans", "uq_analysis_plans_run_hash", "missing"),
            reasons,
        )
        self.assertIn(
            (
                "analysis_runs",
                "uq_analysis_runs_user_run",
                "unexpected TTL index on durable collection",
            ),
            reasons,
        )
        self.assertIn(
            (
                "analysis_plans",
                "legacy_ttl",
                "unexpected TTL index on durable collection",
            ),
            reasons,
        )

    def test_dataset_catalog_has_no_redundant_owned_lookup(self) -> None:
        names = {
            definition.name
            for definition in _definitions_by_collection()["dataset_catalog"]
        }
        self.assertNotIn("ix_dataset_catalog_owned_lookup", names)

    async def test_migration_removes_only_known_redundant_index(self) -> None:
        database = _FakeDatabase()
        collection = database["dataset_catalog"]
        collection.installed.append(
            {
                "name": "ix_dataset_catalog_owned_lookup",
                "key": dict(
                    DEPRECATED_ANALYSIS_INDEXES["dataset_catalog"][
                        "ix_dataset_catalog_owned_lookup"
                    ]
                ),
            }
        )

        report = await migrate_analysis_indexes(database)

        self.assertTrue(report.ok, report.drift)
        installed_names = {
            str(document.get("name")) for document in collection.installed
        }
        self.assertNotIn("ix_dataset_catalog_owned_lookup", installed_names)

    async def test_migration_refuses_changed_legacy_index(self) -> None:
        database = _FakeDatabase()
        collection = database["dataset_catalog"]
        collection.installed.append(
            {
                "name": "ix_dataset_catalog_owned_lookup",
                "key": {"user_id": 1, "unexpected": 1},
            }
        )

        with self.assertRaisesRegex(RuntimeError, "refusing to drop"):
            await migrate_analysis_indexes(database)

    async def test_migration_replaces_exact_pre_pause_queue_indexes(self) -> None:
        database = _FakeDatabase()
        collection = database["analysis_runs"]
        replacements = LEGACY_ANALYSIS_INDEX_REPLACEMENTS["analysis_runs"]
        for definitions in replacements.values():
            legacy = definitions[0]
            collection.installed.append(dict(legacy.build().document))

        report = await migrate_analysis_indexes(database)

        self.assertTrue(report.ok, report.drift)
        installed = {
            str(document.get("name")): document
            for document in collection.installed
        }
        for index_name in replacements:
            expected = _definition_by_name("analysis_runs", index_name)
            self.assertEqual(
                tuple(dict(installed[index_name]["key"]).items()),
                expected.keys,
            )

    async def test_migration_refuses_unknown_named_queue_definition(self) -> None:
        database = _FakeDatabase()
        database["analysis_runs"].installed.append(
            {
                "name": "ix_analysis_runs_recovery_queue",
                "key": {"status": 1, "unknown": 1},
            }
        )

        with self.assertRaisesRegex(RuntimeError, "refusing to replace"):
            await migrate_analysis_indexes(database)

    async def test_indexes_are_durable_and_tenant_scoped_except_worker_queue(
        self,
    ) -> None:
        worker_queue_indexes = {
            "ix_analysis_runs_recovery_queue",
            "ix_analysis_runs_expiration_queue",
            "ix_analysis_runs_pause_queue",
            "ix_artifact_versions_reconciliation_queue",
            # Expired write leases are swept across tenants, like the run
            # queues: a reservation nobody released must not outlive its owner.
            "ix_analysis_write_reservations_expiry_sweep",
        }
        names: set[str] = set()
        for group in ANALYSIS_INDEX_DEFINITIONS:
            for definition in group.indexes:
                self.assertNotIn(definition.name, names)
                names.add(definition.name)
                built = definition.build().document
                self.assertNotIn("expireAfterSeconds", built)
                if definition.name not in worker_queue_indexes:
                    self.assertEqual(definition.keys[0][0], "user_id")

        recovery = _definition_by_name(
            "analysis_runs",
            "ix_analysis_runs_recovery_queue",
        )
        self.assertEqual(
            recovery.keys,
            (
                ("cancellation_requested", 1),
                ("pause_requested", 1),
                ("status", 1),
                ("lease_expires_at", 1),
                ("created_at", 1),
                ("run_id", 1),
            ),
        )
        expiration = _definition_by_name(
            "analysis_runs",
            "ix_analysis_runs_expiration_queue",
        )
        self.assertEqual(
            dict(expiration.partial_filter or {}),
            {"expires_at": {"$type": "date"}},
        )
        self.assertEqual(
            expiration.keys,
            (
                ("cancellation_requested", 1),
                ("pause_requested", 1),
                ("status", 1),
                ("expires_at", 1),
                ("run_id", 1),
                ("lease_expires_at", 1),
            ),
        )
        artifact_reconciliation = _definition_by_name(
            "artifact_versions",
            "ix_artifact_versions_reconciliation_queue",
        )
        self.assertEqual(
            artifact_reconciliation.keys,
            (
                ("status", 1),
                ("updated_at", 1),
                ("version_id", 1),
            ),
        )


if __name__ == "__main__":
    unittest.main()
