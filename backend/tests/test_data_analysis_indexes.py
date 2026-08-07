from __future__ import annotations

import unittest
from typing import Any

from db.indexes.analysis import (
    ANALYSIS_INDEX_DEFINITIONS,
    ensure_analysis_indexes,
)


class _FakeCollection:
    def __init__(self) -> None:
        self.index_batches: list[list[Any]] = []

    async def create_indexes(self, indexes: list[Any]) -> list[str]:
        self.index_batches.append(indexes)
        return [str(index.document["name"]) for index in indexes]


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
                "analysis_patch_proposals",
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
            (
                "analysis_patch_proposals",
                "uq_analysis_patch_proposals_identity",
            ): (
                ("user_id", 1),
                ("run_id", 1),
                ("patch_id", 1),
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

    async def test_indexes_are_durable_and_tenant_scoped_except_worker_queue(
        self,
    ) -> None:
        worker_queue_indexes = {
            "ix_analysis_runs_recovery_queue",
            "ix_analysis_runs_expiration_queue",
            "ix_analysis_runs_pause_queue",
            "ix_artifact_versions_reconciliation_queue",
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
