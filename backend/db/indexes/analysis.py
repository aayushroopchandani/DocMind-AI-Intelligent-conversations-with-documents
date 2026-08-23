from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.errors import OperationFailure


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


@dataclass(frozen=True, slots=True)
class MongoIndexDrift:
    """One missing or incompatible durable index discovered by verification."""

    collection_name: str
    index_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class MongoIndexVerificationReport:
    """Read-only comparison between declared and installed Phase-8 indexes."""

    drift: tuple[MongoIndexDrift, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.drift


DEPRECATED_ANALYSIS_INDEXES: Mapping[
    str,
    Mapping[str, tuple[tuple[str, int], ...]],
] = MappingProxyType(
    {
        "dataset_catalog": MappingProxyType(
            {
                "ix_dataset_catalog_owned_lookup": (
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("dataset_id", ASCENDING),
                    ("source_version", ASCENDING),
                )
            }
        )
    }
)


LEGACY_ANALYSIS_INDEX_REPLACEMENTS: Mapping[
    str,
    Mapping[str, tuple[MongoIndexDefinition, ...]],
] = MappingProxyType(
    {
        "analysis_patch_proposals": MappingProxyType(
            {
                # Phase 9.12 added patch revisions. This is the exact pre-9.12
                # definition that may be replaced.
                "uq_analysis_patch_proposals_identity": (
                    MongoIndexDefinition(
                        keys=(
                            ("user_id", ASCENDING),
                            ("run_id", ASCENDING),
                            ("patch_id", ASCENDING),
                        ),
                        name="uq_analysis_patch_proposals_identity",
                        unique=True,
                    ),
                ),
            }
        ),
        "analysis_runs": MappingProxyType(
            {
                # Phase 8.10 added pause fencing to both worker queues. These
                # are the exact pre-8.10 definitions that may be replaced.
                "ix_analysis_runs_recovery_queue": (
                    MongoIndexDefinition(
                        keys=(
                            ("cancellation_requested", ASCENDING),
                            ("status", ASCENDING),
                            ("lease_expires_at", ASCENDING),
                            ("created_at", ASCENDING),
                            ("run_id", ASCENDING),
                        ),
                        name="ix_analysis_runs_recovery_queue",
                    ),
                ),
                "ix_analysis_runs_expiration_queue": (
                    MongoIndexDefinition(
                        keys=(
                            ("cancellation_requested", ASCENDING),
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
                ),
            }
        )
    }
)


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
                # Deliberately non-unique: an unchanged byte stream may be a
                # meaningful audited version with different parent/metadata.
                # Retry idempotency is enforced by version_id instead.
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
                    ("run_id", ASCENDING),
                    ("plan_hash", ASCENDING),
                ),
                name="uq_analysis_plans_run_hash",
                unique=True,
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workspace_id", ASCENDING),
                    ("created_at", DESCENDING),
                    ("plan_id", DESCENDING),
                ),
                name="ix_analysis_plans_workspace_history",
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
        collection_name="analysis_executions",
        indexes=(
            # `reserve` is a conditional insert that relies on a duplicate-key
            # error to resolve a race between two workers claiming the same
            # deterministic execution key (9.3.3). Without this index that
            # insert always succeeds and the "exactly one execution per key"
            # guarantee is only as good as the read that preceded it.
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("execution_key", ASCENDING),
                ),
                name="uq_analysis_executions_key",
                unique=True,
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("execution_id", ASCENDING),
                ),
                name="uq_analysis_executions_identity",
                unique=True,
            ),
            # Readers arrive holding a run, not a key. Newest-first so the
            # lookup is a single indexed document rather than a sort.
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("run_id", ASCENDING),
                    ("created_at", DESCENDING),
                ),
                name="ix_analysis_executions_run",
            ),
        ),
    ),
    CollectionIndexDefinitions(
        collection_name="analysis_patch_proposals",
        indexes=(
            # A rebase or relocation issues a new revision of the same patch,
            # so identity is the pair, not the patch alone (9.12.1).
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("run_id", ASCENDING),
                    ("patch_id", ASCENDING),
                    ("revision", ASCENDING),
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
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("run_id", ASCENDING),
                    ("created_at", DESCENDING),
                    ("revision", DESCENDING),
                ),
                name="ix_analysis_patch_proposals_run_history",
            ),
        ),
    ),
    CollectionIndexDefinitions(
        collection_name="analysis_write_reservations",
        indexes=(
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("reservation_id", ASCENDING),
                ),
                name="uq_analysis_write_reservations_identity",
                unique=True,
            ),
            # The overlap query (9.11.5): sheet first, then the four interval
            # bounds it compares. MongoDB cannot enforce non-overlap with an
            # index, so this exists to make the repository's check cheap rather
            # than to enforce anything by itself.
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("workbook_id", ASCENDING),
                    ("worksheet_id", ASCENDING),
                    ("status", ASCENDING),
                    ("expires_at", ASCENDING),
                    ("first_row", ASCENDING),
                    ("last_row", ASCENDING),
                    ("first_column", ASCENDING),
                    ("last_column", ASCENDING),
                ),
                name="ix_analysis_write_reservations_overlap",
            ),
            MongoIndexDefinition(
                keys=(
                    ("user_id", ASCENDING),
                    ("run_id", ASCENDING),
                    ("patch_id", ASCENDING),
                    ("patch_revision", ASCENDING),
                ),
                name="uq_analysis_write_reservations_patch_revision",
                unique=True,
                partial_filter=MappingProxyType({"status": "active"}),
            ),
            MongoIndexDefinition(
                keys=(
                    ("status", ASCENDING),
                    ("expires_at", ASCENDING),
                ),
                name="ix_analysis_write_reservations_expiry_sweep",
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


async def verify_analysis_indexes(database: Any) -> MongoIndexVerificationReport:
    """Verify index drift without creating, deleting, or rebuilding indexes.

    Startup uses this read-only contract. Index installation/replacement stays
    in the explicit migration command so serving traffic never changes schema.
    """

    drift: list[MongoIndexDrift] = []
    for definitions in ANALYSIS_INDEX_DEFINITIONS:
        installed = await _list_installed_indexes(
            database[definitions.collection_name]
        )
        expected_names = {definition.name for definition in definitions.indexes}
        for definition in definitions.indexes:
            actual = installed.get(definition.name)
            if actual is None:
                drift.append(
                    MongoIndexDrift(
                        collection_name=definitions.collection_name,
                        index_name=definition.name,
                        reason="missing",
                    )
                )
                continue
            if _installed_index_keys(actual) != definition.keys:
                drift.append(
                    MongoIndexDrift(
                        collection_name=definitions.collection_name,
                        index_name=definition.name,
                        reason="key pattern differs",
                    )
                )
            if bool(actual.get("unique", False)) != definition.unique:
                drift.append(
                    MongoIndexDrift(
                        collection_name=definitions.collection_name,
                        index_name=definition.name,
                        reason="unique option differs",
                    )
                )
            expected_partial = dict(definition.partial_filter or {})
            actual_partial = dict(actual.get("partialFilterExpression") or {})
            if actual_partial != expected_partial:
                drift.append(
                    MongoIndexDrift(
                        collection_name=definitions.collection_name,
                        index_name=definition.name,
                        reason="partial filter differs",
                    )
                )
            if "expireAfterSeconds" in actual:
                drift.append(
                    MongoIndexDrift(
                        collection_name=definitions.collection_name,
                        index_name=definition.name,
                        reason="unexpected TTL index on durable collection",
                    )
                )

        # Phase-8 control-plane history is durable. Flag accidental TTL even
        # when it appears on an unexpected, legacy index.
        for name, document in installed.items():
            if name == "_id_" or name in expected_names:
                continue
            if "expireAfterSeconds" in document:
                drift.append(
                    MongoIndexDrift(
                        collection_name=definitions.collection_name,
                        index_name=name,
                        reason="unexpected TTL index on durable collection",
                    )
                )
            if name in DEPRECATED_ANALYSIS_INDEXES.get(
                definitions.collection_name,
                {},
            ):
                drift.append(
                    MongoIndexDrift(
                        collection_name=definitions.collection_name,
                        index_name=name,
                        reason="deprecated redundant index",
                    )
                )
    return MongoIndexVerificationReport(drift=tuple(drift))


async def migrate_analysis_indexes(database: Any) -> MongoIndexVerificationReport:
    """Apply only declared, exact legacy replacements and removals."""

    # A changed index cannot be recreated under the same stable name until its
    # known legacy definition is removed. Unknown drift fails closed.
    current_definitions = {
        group.collection_name: {
            definition.name: definition for definition in group.indexes
        }
        for group in ANALYSIS_INDEX_DEFINITIONS
    }
    for collection_name, replacements in (
        LEGACY_ANALYSIS_INDEX_REPLACEMENTS.items()
    ):
        collection = database[collection_name]
        installed = await _list_installed_indexes(collection)
        for index_name, legacy_definitions in replacements.items():
            document = installed.get(index_name)
            if document is None:
                continue
            current = current_definitions[collection_name][index_name]
            if _installed_index_matches(document, current):
                continue
            if not any(
                _installed_index_matches(document, legacy)
                for legacy in legacy_definitions
            ):
                raise RuntimeError(
                    f"refusing to replace unknown index definition "
                    f"{collection_name}.{index_name}"
                )
            await collection.drop_index(index_name)

    await ensure_analysis_indexes(database)
    for collection_name, deprecated in DEPRECATED_ANALYSIS_INDEXES.items():
        collection = database[collection_name]
        installed = await _list_installed_indexes(collection)
        for index_name, expected_keys in deprecated.items():
            document = installed.get(index_name)
            if document is None:
                continue
            if (
                _installed_index_keys(document) != expected_keys
                or bool(document.get("unique", False))
                or bool(document.get("partialFilterExpression"))
                or "expireAfterSeconds" in document
            ):
                raise RuntimeError(
                    f"refusing to drop changed legacy index "
                    f"{collection_name}.{index_name}"
                )
            await collection.drop_index(index_name)
    return await verify_analysis_indexes(database)


def _installed_index_keys(
    document: Mapping[str, object],
) -> tuple[tuple[str, int], ...]:
    return tuple(
        (str(key), int(direction))
        for key, direction in dict(document.get("key") or {}).items()
    )


async def _list_installed_indexes(
    collection: Any,
) -> dict[str, Mapping[str, object]]:
    try:
        return {
            str(document.get("name") or ""): document
            async for document in collection.list_indexes()
        }
    except OperationFailure as exc:
        # listIndexes on a not-yet-created collection is an empty definition,
        # not an operational outage. Any other server error must remain loud.
        if exc.code == 26:  # NamespaceNotFound
            return {}
        raise


def _installed_index_matches(
    document: Mapping[str, object],
    definition: MongoIndexDefinition,
) -> bool:
    return (
        _installed_index_keys(document) == definition.keys
        and bool(document.get("unique", False)) == definition.unique
        and dict(document.get("partialFilterExpression") or {})
        == dict(definition.partial_filter or {})
        and "expireAfterSeconds" not in document
    )


__all__ = [
    "ANALYSIS_INDEX_DEFINITIONS",
    "DEPRECATED_ANALYSIS_INDEXES",
    "LEGACY_ANALYSIS_INDEX_REPLACEMENTS",
    "CollectionIndexDefinitions",
    "MongoIndexDrift",
    "MongoIndexDefinition",
    "MongoIndexVerificationReport",
    "ensure_analysis_indexes",
    "migrate_analysis_indexes",
    "verify_analysis_indexes",
]
