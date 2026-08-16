"""Durable input resolution (Phase 9.3.1).

Planning happens in one worker lease; execution may happen in another, after a
restart, an approval, or a resume. The executor therefore cannot rely on the
Phase 7 results that happened to be in memory when the plan was written. It
resolves each input again from persisted state, keyed by tenant, dataset and
version, and re-verifies that what it found is what the plan was built against.

Phase 9.3.1 is explicit that a recipe hash alone is not a data version, because
two different source tables can share a recipe. Verification therefore compares
source identities and source versions from the plan's provenance as well.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from pydantic import JsonValue, ValidationError

from scripts.data_analysis_agent.analysis.models.preparation import (
    MaterializationType,
    NormalizedDatasetReference,
)

from ..models.plans import PlanColumn, PlanInputDataset
from .contracts import ExecutionFailureCode
from .idempotency import dataset_content_signature


class InputResolutionError(RuntimeError):
    """An input could not be resolved, or does not match the plan."""

    def __init__(
        self,
        code: ExecutionFailureCode,
        message: str,
        *,
        dataset_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.dataset_id = dataset_id


@dataclass(frozen=True, slots=True)
class ResolvedInput:
    """One verified input, ready to be staged for the engine."""

    alias: str
    dataset_id: str
    content_signature: str
    columns: tuple[PlanColumn, ...]
    rows: tuple[dict[str, JsonValue], ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)


class NormalizedInputResolver(Protocol):
    async def resolve(
        self,
        *,
        user_id: str,
        workspace_id: str,
        datasets: Sequence[PlanInputDataset],
    ) -> tuple[ResolvedInput, ...]: ...


def verify_reference(
    dataset: PlanInputDataset,
    reference: NormalizedDatasetReference,
) -> None:
    """Raise unless a stored reference still matches the plan it was built for."""

    if reference.recipe_hash != dataset.dataset_version:
        raise InputResolutionError(
            ExecutionFailureCode.INPUT_VERSION_MISMATCH,
            f"dataset '{dataset.dataset_id}' was normalized by a different recipe",
            dataset_id=dataset.dataset_id,
        )
    planned_sources = tuple(
        (item.source_dataset_id, item.source_version) for item in dataset.provenance
    )
    stored_sources = tuple(
        zip(reference.source_dataset_ids, reference.source_versions, strict=True)
    )
    if sorted(planned_sources) != sorted(stored_sources):
        raise InputResolutionError(
            ExecutionFailureCode.INPUT_VERSION_MISMATCH,
            f"dataset '{dataset.dataset_id}' now resolves to different sources "
            "or source versions than the plan was built against",
            dataset_id=dataset.dataset_id,
        )
    planned_columns = tuple(column.key for column in dataset.columns)
    stored_columns = tuple(column.key for column in reference.columns)
    if planned_columns != stored_columns:
        raise InputResolutionError(
            ExecutionFailureCode.INPUT_VERSION_MISMATCH,
            f"dataset '{dataset.dataset_id}' no longer has the planned schema",
            dataset_id=dataset.dataset_id,
        )
    if reference.output_row_count != dataset.row_count:
        raise InputResolutionError(
            ExecutionFailureCode.INPUT_VERSION_MISMATCH,
            f"dataset '{dataset.dataset_id}' row count changed since planning",
            dataset_id=dataset.dataset_id,
        )


def verify_rows(
    dataset: PlanInputDataset,
    rows: Sequence[dict[str, Any]],
) -> tuple[dict[str, JsonValue], ...]:
    """Project stored rows onto the planned schema, rejecting any gap."""

    expected = tuple(column.key for column in dataset.columns)
    if len(rows) != dataset.row_count:
        raise InputResolutionError(
            ExecutionFailureCode.INPUT_VERSION_MISMATCH,
            f"dataset '{dataset.dataset_id}' returned {len(rows)} rows, "
            f"but the plan was built against {dataset.row_count}",
            dataset_id=dataset.dataset_id,
        )
    projected: list[dict[str, JsonValue]] = []
    for row in rows:
        missing = [key for key in expected if key not in row]
        if missing:
            raise InputResolutionError(
                ExecutionFailureCode.INPUT_VERSION_MISMATCH,
                f"dataset '{dataset.dataset_id}' is missing planned columns: "
                + ", ".join(sorted(missing)),
                dataset_id=dataset.dataset_id,
            )
        projected.append({key: row[key] for key in expected})
    return tuple(projected)


class MongoNormalizedInputResolver:
    """Resolve plan inputs from the durable normalized-dataset records.

    Materialized datasets carry their rows in the normalized record. Passthrough
    datasets were not transformed, so their rows still live in the immutable
    source the record points at, and are re-read from there.
    """

    collection_name = "normalized_datasets"
    structured_tables_collection = "structured_tables"

    def __init__(
        self,
        database: Any | None = None,
        *,
        blob_store: Any | None = None,
    ) -> None:
        self._database = database
        self._blob_store = blob_store

    def _db(self) -> Any:
        if self._database is not None:
            return self._database
        from db.mongodb import get_db

        return get_db()

    async def resolve(
        self,
        *,
        user_id: str,
        workspace_id: str,
        datasets: Sequence[PlanInputDataset],
    ) -> tuple[ResolvedInput, ...]:
        if not datasets:
            return ()
        references = await self._load_references(
            user_id=user_id,
            dataset_ids=tuple(item.dataset_id for item in datasets),
        )
        resolved: list[ResolvedInput] = []
        for dataset in datasets:
            found = references.get(dataset.dataset_id)
            if found is None:
                raise InputResolutionError(
                    ExecutionFailureCode.INPUT_UNAVAILABLE,
                    f"normalized dataset '{dataset.dataset_id}' is no longer "
                    "available for this workspace",
                    dataset_id=dataset.dataset_id,
                )
            reference, document = found
            verify_reference(dataset, reference)
            rows = await self._load_rows(
                user_id=user_id,
                dataset=dataset,
                reference=reference,
                document=document,
            )
            resolved.append(
                ResolvedInput(
                    alias=dataset.alias,
                    dataset_id=dataset.dataset_id,
                    content_signature=dataset_content_signature(dataset),
                    columns=dataset.columns,
                    rows=verify_rows(dataset, rows),
                )
            )
        return tuple(resolved)

    async def _load_references(
        self,
        *,
        user_id: str,
        dataset_ids: tuple[str, ...],
    ) -> dict[str, tuple[NormalizedDatasetReference, dict[str, Any]]]:
        unique = tuple(dict.fromkeys(dataset_ids))
        cursor = self._db()[self.collection_name].find(
            {
                "user_id": user_id,
                "normalized_dataset_id": {"$in": list(unique)},
            }
        )
        documents = await cursor.to_list(length=len(unique))
        output: dict[str, tuple[NormalizedDatasetReference, dict[str, Any]]] = {}
        for document in documents:
            try:
                reference = NormalizedDatasetReference.model_validate(
                    document.get("reference")
                )
            except ValidationError:
                continue
            output[reference.normalized_dataset_id] = (reference, dict(document))
        return output

    async def _load_rows(
        self,
        *,
        user_id: str,
        dataset: PlanInputDataset,
        reference: NormalizedDatasetReference,
        document: dict[str, Any],
    ) -> Sequence[dict[str, Any]]:
        if reference.materialization == MaterializationType.MATERIALIZED_DATASET:
            rows = document.get("rows")
            if not isinstance(rows, list):
                raise InputResolutionError(
                    ExecutionFailureCode.INPUT_UNAVAILABLE,
                    f"materialized dataset '{dataset.dataset_id}' has no stored rows",
                    dataset_id=dataset.dataset_id,
                )
            return [dict(row) for row in rows]

        access = reference.access
        if access.provider == "mongodb":
            return await self._load_source_table_rows(
                user_id=user_id,
                dataset=dataset,
                record_id=access.record_id,
            )
        return await self._load_blob_rows(
            user_id=user_id,
            dataset=dataset,
            reference=reference,
        )

    async def _load_source_table_rows(
        self,
        *,
        user_id: str,
        dataset: PlanInputDataset,
        record_id: str,
    ) -> Sequence[dict[str, Any]]:
        document = await self._db()[self.structured_tables_collection].find_one(
            {"user_id": user_id, "table_id": record_id}
        )
        rows = (document or {}).get("rows")
        if not isinstance(rows, list):
            raise InputResolutionError(
                ExecutionFailureCode.INPUT_UNAVAILABLE,
                f"source table for '{dataset.dataset_id}' is no longer readable",
                dataset_id=dataset.dataset_id,
            )
        return [dict(row) for row in rows]

    async def _load_blob_rows(
        self,
        *,
        user_id: str,
        dataset: PlanInputDataset,
        reference: NormalizedDatasetReference,
    ) -> Sequence[dict[str, Any]]:
        from scripts.data_analysis_agent.analysis.repositories.datasets import (
            MongoDatasetRepository,
            _blob_encoding,
        )
        from scripts.data_analysis_agent.runtime.models.artifacts import BlobReference
        from config.settings import settings

        payload = reference.access.blob
        if payload is None:
            raise InputResolutionError(
                ExecutionFailureCode.INPUT_UNAVAILABLE,
                f"passthrough dataset '{dataset.dataset_id}' has no blob locator",
                dataset_id=dataset.dataset_id,
            )
        repository = MongoDatasetRepository(blob_store=self._blob_store)
        try:
            blob = BlobReference.model_validate(payload)
            content = await repository._configured_blob_store().download(
                blob,
                max_bytes=settings.analysis_max_artifact_bytes,
            )
            table = repository._decode_tabular_blob(
                content,
                encoding=_blob_encoding(blob.filename),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise InputResolutionError(
                ExecutionFailureCode.INPUT_UNAVAILABLE,
                f"artifact rows for '{dataset.dataset_id}' could not be loaded",
                dataset_id=dataset.dataset_id,
            ) from exc
        if table.user_id != user_id:
            raise InputResolutionError(
                ExecutionFailureCode.INPUT_UNAVAILABLE,
                f"artifact rows for '{dataset.dataset_id}' failed tenant verification",
                dataset_id=dataset.dataset_id,
            )
        return [dict(row) for row in table.rows]


__all__ = [
    "InputResolutionError",
    "MongoNormalizedInputResolver",
    "NormalizedInputResolver",
    "ResolvedInput",
    "verify_reference",
    "verify_rows",
]
