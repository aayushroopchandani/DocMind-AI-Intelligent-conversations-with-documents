from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from pymongo import ASCENDING, DESCENDING, IndexModel


@dataclass(frozen=True, slots=True)
class MongoIndexDefinition:
    """Declarative index definition with a stable, migration-friendly name."""

    keys: tuple[tuple[str, int], ...]
    name: str
    unique: bool = False
    partial_filter: Mapping[str, object] | None = None

    def build(self) -> IndexModel:
        options: dict[str, object] = {
            "name": self.name,
            "unique": self.unique,
        }
        if self.partial_filter is not None:
            options["partialFilterExpression"] = dict(self.partial_filter)
        return IndexModel(list(self.keys), **options)


@dataclass(frozen=True, slots=True)
class CollectionIndexDefinitions:
    collection_name: str
    indexes: tuple[MongoIndexDefinition, ...]


ANALYSIS_INDEX_DEFINITIONS = (
    CollectionIndexDefinitions(
        collection_name="analysis_runs",
        indexes=(
            MongoIndexDefinition(
                keys=(("user_id", ASCENDING), ("run_id", ASCENDING)),
                name="uq_analysis_runs_user_run",
                unique=True,
            ),
            MongoIndexDefinition(
                keys=(("user_id", ASCENDING), ("idempotency_key", ASCENDING)),
                name="uq_analysis_runs_user_idempotency",
                unique=True,
                # Allows a safe migration if pre-Phase-8 records exist without
                # an idempotency key, while enforcing it for every new run.
                partial_filter=MappingProxyType(
                    {"idempotency_key": {"$type": "string"}}
                ),
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("created_at", DESCENDING),
                    ("run_id", DESCENDING),
                ),
                name="ix_analysis_runs_workspace_history",
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("status", ASCENDING),
                    ("created_at", DESCENDING),
                    ("run_id", DESCENDING),
                ),
                name="ix_analysis_runs_workspace_status_history",
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("status", ASCENDING),
                    ("updated_at", DESCENDING),
                    ("run_id", DESCENDING),
                ),
                name="ix_analysis_runs_user_status_activity",
            ),
            # Cross-tenant indexes below serve internal worker queues, never
            # user-facing reads.
            MongoIndexDefinition(
                keys=(
                    ("cancellation_requested", ASCENDING),
                    ("pause_requested", ASCENDING),
                    ("status", ASCENDING),
                    ("lease_expires_at", ASCENDING),
                    ("created_at", ASCENDING),
                    ("run_id", ASCENDING),
                ),
                name="ix_analysis_runs_recovery_queue",
            ),
            MongoIndexDefinition(
                keys=(
                    ("cancellation_requested", ASCENDING),
                    ("pause_requested", ASCENDING),
                    ("status", ASCENDING),
                    ("expires_at", ASCENDING),
                    ("run_id", ASCENDING),
                    ("lease_expires_at", ASCENDING),
                ),
                name="ix_analysis_runs_expiration_queue",
                partial_filter=MappingProxyType(
                    {"expires_at": {"$type": "date"}}
                ),
            ),
            MongoIndexDefinition(
                keys=(
                    ("pause_requested", ASCENDING),
                    ("status", ASCENDING),
                    ("lease_expires_at", ASCENDING),
                    ("pause_requested_at", ASCENDING),
                    ("run_id", ASCENDING),
                ),
                name="ix_analysis_runs_pause_queue",
            ),
        ),
    ),
    CollectionIndexDefinitions(
        collection_name="analysis_run_events",
        indexes=(
            # The unique constraint is also the SSE replay index because a
            # range scan over sequence is ordered within one tenant/run.
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("run_id", ASCENDING),
                    ("sequence", ASCENDING),
                ),
                name="uq_analysis_run_events_sequence",
                unique=True,
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("run_id", ASCENDING),
                    ("deduplication_key", ASCENDING),
                ),
                name="uq_analysis_run_events_deduplication",
                unique=True,
                # Most lifecycle events do not need a deduplication key. A
                # type-based partial index permits multiple null/missing keys
                # while making every supplied key unique.
                partial_filter=MappingProxyType(
                    {"deduplication_key": {"$type": "string"}}
                ),
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("run_id", ASCENDING),
                    ("occurred_at", ASCENDING),
                    ("sequence", ASCENDING),
                ),
                name="ix_analysis_run_events_chronology",
            ),
        ),
    ),
    CollectionIndexDefinitions(
        collection_name="workspace_artifacts",
        indexes=(
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("artifact_id", ASCENDING),
                ),
                name="uq_workspace_artifacts_identity",
                unique=True,
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("updated_at", DESCENDING),
                    ("artifact_id", ASCENDING),
                ),
                name="ix_workspace_artifacts_history",
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("artifact_type", ASCENDING),
                    ("updated_at", DESCENDING),
                ),
                name="ix_workspace_artifacts_type",
            ),
        ),
    ),
    CollectionIndexDefinitions(
        collection_name="artifact_versions",
        indexes=(
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("version_id", ASCENDING),
                ),
                name="uq_artifact_versions_identity",
                unique=True,
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("artifact_id", ASCENDING),
                    ("version_number", DESCENDING),
                ),
                name="uq_artifact_versions_number",
                unique=True,
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("created_at", DESCENDING),
                    ("version_id", ASCENDING),
                ),
                name="ix_artifact_versions_workspace_history",
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("artifact_id", ASCENDING),
                    ("content_hash", ASCENDING),
                ),
                name="ix_artifact_versions_content",
            ),
            # Internal, cross-tenant recovery queue. Status and age are the
            # complete eligibility predicate; version_id makes bounded scans
            # deterministic when timestamps collide.
            MongoIndexDefinition(
                keys=(
                    ("status", ASCENDING),
                    ("updated_at", ASCENDING),
                    ("version_id", ASCENDING),
                ),
                name="ix_artifact_versions_reconciliation_queue",
            ),
        ),
    ),
    CollectionIndexDefinitions(
        collection_name="dataset_catalog",
        indexes=(
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("dataset_id", ASCENDING),
                    ("source_version", ASCENDING),
                ),
                name="uq_dataset_catalog_version",
                unique=True,
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("dataset_id", ASCENDING),
                    ("source_version", ASCENDING),
                ),
                name="ix_dataset_catalog_owned_lookup",
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("source_type", ASCENDING),
                    ("registered_at", DESCENDING),
                    ("dataset_id", ASCENDING),
                ),
                name="ix_dataset_catalog_source_history",
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("artifact_id", ASCENDING),
                    ("artifact_version_id", ASCENDING),
                ),
                name="ix_dataset_catalog_artifact_version",
                partial_filter=MappingProxyType(
                    {
                        "artifact_id": {"$type": "string"},
                        "artifact_version_id": {"$type": "string"},
                    }
                ),
            ),
        ),
    ),
    CollectionIndexDefinitions(
        collection_name="analysis_plans",
        indexes=(
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("run_id", ASCENDING),
                    ("revision", ASCENDING),
                ),
                name="uq_analysis_plans_run_revision",
                unique=True,
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("run_id", ASCENDING),
                    ("plan_id", ASCENDING),
                ),
                name="uq_analysis_plans_identity",
                unique=True,
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("write_target_keys", ASCENDING),
                ),
                name="uq_analysis_plans_active_write_reservations",
                unique=True,
                partial_filter=MappingProxyType(
                    {"reservation_active": True}
                ),
            ),
        ),
    ),
    CollectionIndexDefinitions(
        collection_name="analysis_patch_proposals",
        indexes=(
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("run_id", ASCENDING),
                    ("patch_id", ASCENDING),
                ),
                name="uq_analysis_patch_proposals_identity",
                unique=True,
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("approval.status", ASCENDING),
                    ("created_at", DESCENDING),
                ),
                name="ix_analysis_patch_proposals_approval_queue",
            ),
        ),
    ),
)


async def ensure_analysis_indexes(database: Any) -> None:
    """Create all Phase-8 indexes, batching one command per collection."""

    for definitions in ANALYSIS_INDEX_DEFINITIONS:
        await database[definitions.collection_name].create_indexes(
            [index.build() for index in definitions.indexes]
        )


__all__ = [
    "ANALYSIS_INDEX_DEFINITIONS",
    "CollectionIndexDefinitions",
    "MongoIndexDefinition",
    "ensure_analysis_indexes",
]
