"""Opt-in checks against an isolated real MongoDB replica-set database.

Set ``MONGODB_TEST_URI`` to run these tests. The suite creates and drops only a
randomly named ``docmind_phase8_test_*`` database; it never touches MONGODB_URI.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from db.indexes.analysis import ensure_analysis_indexes, verify_analysis_indexes


_TEST_URI = os.getenv("MONGODB_TEST_URI", "").strip()


@unittest.skipUnless(_TEST_URI, "MONGODB_TEST_URI is not configured")
class Phase8MongoReplicaIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = AsyncIOMotorClient(_TEST_URI)
        hello = await self.client.admin.command("hello")
        if not hello.get("setName") and hello.get("msg") != "isdbgrid":
            self.skipTest("MONGODB_TEST_URI must use a replica set or mongos")
        self.database_name = f"docmind_phase8_test_{uuid4().hex}"
        self.database = self.client[self.database_name]
        await ensure_analysis_indexes(self.database)

    async def asyncTearDown(self) -> None:
        if hasattr(self, "database_name"):
            await self.client.drop_database(self.database_name)
        self.client.close()

    async def test_declared_indexes_install_without_drift(self) -> None:
        report = await verify_analysis_indexes(self.database)

        self.assertTrue(report.ok, report.drift)

    async def test_run_and_creation_event_commit_atomically(self) -> None:
        run_id = str(uuid4())
        async with await self.client.start_session() as session:
            async with session.start_transaction():
                await self.database.analysis_runs.insert_one(
                    {
                        "user_id": "user-1",
                        "run_id": run_id,
                        "idempotency_key": "integration-key-1",
                        "workspace_id": "workspace-1",
                        "status": "created",
                    },
                    session=session,
                )
                await self.database.analysis_run_events.insert_one(
                    {
                        "user_id": "user-1",
                        "run_id": run_id,
                        "sequence": 1,
                    },
                    session=session,
                )

        self.assertIsNotNone(
            await self.database.analysis_runs.find_one({"run_id": run_id})
        )
        self.assertIsNotNone(
            await self.database.analysis_run_events.find_one({"run_id": run_id})
        )

    async def test_transaction_rollback_leaves_no_partial_control_plane(self) -> None:
        run_id = str(uuid4())
        async with await self.client.start_session() as session:
            async with session.start_transaction():
                await self.database.analysis_runs.insert_one(
                    {
                        "user_id": "user-1",
                        "run_id": run_id,
                        "idempotency_key": "rollback-key-1",
                    },
                    session=session,
                )
                await session.abort_transaction()

        self.assertIsNone(
            await self.database.analysis_runs.find_one({"run_id": run_id})
        )

    async def test_unique_indexes_fence_concurrent_duplicates(self) -> None:
        run_id = str(uuid4())

        async def insert(index: int) -> str:
            try:
                await self.database.analysis_runs.insert_one(
                    {
                        "user_id": "user-1",
                        "run_id": run_id,
                        "idempotency_key": f"concurrent-key-{index}",
                    }
                )
                return "inserted"
            except DuplicateKeyError:
                return "duplicate"

        outcomes = await asyncio.gather(insert(1), insert(2))

        self.assertEqual(sorted(outcomes), ["duplicate", "inserted"])

    async def test_artifact_version_race_and_content_hash_policy(self) -> None:
        artifact_id = str(uuid4())

        async def insert_version(version_id: str) -> str:
            try:
                await self.database.artifact_versions.insert_one(
                    {
                        "user_id": "user-1",
                        "workspace_id": "workspace-1",
                        "artifact_id": artifact_id,
                        "version_id": version_id,
                        "version_number": 1,
                        "content_hash": "same-content",
                    }
                )
                return "inserted"
            except DuplicateKeyError:
                return "duplicate"

        outcomes = await asyncio.gather(
            insert_version(str(uuid4())),
            insert_version(str(uuid4())),
        )
        self.assertEqual(sorted(outcomes), ["duplicate", "inserted"])

        # Identical bytes in a later meaningful version remain valid by policy.
        await self.database.artifact_versions.insert_one(
            {
                "user_id": "user-1",
                "workspace_id": "workspace-1",
                "artifact_id": artifact_id,
                "version_id": str(uuid4()),
                "version_number": 2,
                "content_hash": "same-content",
            }
        )

    async def test_event_sequence_and_write_reservation_are_unique(self) -> None:
        run_id = str(uuid4())

        async def append_event(event_id: str) -> str:
            try:
                await self.database.analysis_run_events.insert_one(
                    {
                        "user_id": "user-1",
                        "run_id": run_id,
                        "event_id": event_id,
                        "sequence": 1,
                    }
                )
                return "inserted"
            except DuplicateKeyError:
                return "duplicate"

        event_outcomes = await asyncio.gather(
            append_event(str(uuid4())),
            append_event(str(uuid4())),
        )
        self.assertEqual(sorted(event_outcomes), ["duplicate", "inserted"])

        reservation = {
            "user_id": "user-1",
            "workspace_id": "workspace-1",
            "write_target_keys": ["workbook-1:sheet-1"],
            "reservation_active": True,
        }
        await self.database.analysis_plans.insert_one(
            {
                **reservation,
                "run_id": str(uuid4()),
                "plan_id": str(uuid4()),
                "revision": 1,
                "plan_hash": "a" * 64,
            }
        )
        with self.assertRaises(DuplicateKeyError):
            await self.database.analysis_plans.insert_one(
                {
                    **reservation,
                    "run_id": str(uuid4()),
                    "plan_id": str(uuid4()),
                    "revision": 1,
                    "plan_hash": "b" * 64,
                }
            )

    async def test_workspace_history_query_uses_declared_index(self) -> None:
        await self.database.analysis_runs.insert_one(
            {
                "user_id": "user-1",
                "workspace_id": "workspace-1",
                "run_id": str(uuid4()),
                "idempotency_key": "explain-key-1",
                "created_at": 1,
            }
        )
        explanation = await self.database.command(
            {
                "explain": {
                    "find": "analysis_runs",
                    "filter": {
                        "user_id": "user-1",
                        "workspace_id": "workspace-1",
                    },
                    "sort": {"created_at": -1, "run_id": -1},
                },
                "verbosity": "queryPlanner",
            }
        )

        serialized = str(explanation.get("queryPlanner", {}))
        self.assertIn("ix_analysis_runs_workspace_history", serialized)

    async def test_event_replay_and_plan_history_indexes_are_usable(self) -> None:
        run_id = str(uuid4())
        event_explanation = await self.database.command(
            {
                "explain": {
                    "find": "analysis_run_events",
                    "filter": {"user_id": "user-1", "run_id": run_id},
                    "sort": {"sequence": 1},
                    "hint": "uq_analysis_run_events_sequence",
                },
                "verbosity": "queryPlanner",
            }
        )
        plan_explanation = await self.database.command(
            {
                "explain": {
                    "find": "analysis_plans",
                    "filter": {
                        "user_id": "user-1",
                        "workspace_id": "workspace-1",
                    },
                    "sort": {"created_at": -1, "plan_id": -1},
                    "hint": "ix_analysis_plans_workspace_history",
                },
                "verbosity": "queryPlanner",
            }
        )

        self.assertIn(
            "uq_analysis_run_events_sequence",
            str(event_explanation.get("queryPlanner", {})),
        )
        self.assertIn(
            "ix_analysis_plans_workspace_history",
            str(plan_explanation.get("queryPlanner", {})),
        )


if __name__ == "__main__":
    unittest.main()
