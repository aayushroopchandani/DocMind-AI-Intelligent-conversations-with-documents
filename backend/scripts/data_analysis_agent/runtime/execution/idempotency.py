"""Deterministic execution keys and result reuse (Phase 9.3.3).

The key answers one question: "has this exact computation, over these exact
immutable inputs, already produced a verified result?" Everything that can
change the answer is in the key; nothing that cannot is.

The key is tenant-scoped on purpose. Two users can upload byte-identical files,
and a shared key would let one of them observe that the other's identical data
already exists. Scoping by user and workspace removes that channel entirely.
"""

from __future__ import annotations

import hashlib
import json

from ..models.canonical import canonical_content
from ..models.plans import AnalysisPlan, PlanInputDataset, PlanStep
from .native import semantics


EXECUTION_KEY_VERSION = "1.0"


def dataset_content_signature(dataset: PlanInputDataset) -> str:
    """Return a signature binding a normalized dataset to its exact sources.

    Phase 9.3.1 is explicit that a recipe hash alone is not a data version,
    because two different source tables can share a recipe. The signature
    therefore covers the source identities and source versions carried in
    provenance, not just `dataset_version`.
    """

    return _sha256(
        {
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version,
            "row_count": dataset.row_count,
            "columns": canonical_content(dataset.columns),
            "provenance": canonical_content(dataset.provenance),
        }
    )


def native_recipe_hash(steps: tuple[PlanStep, ...], result_alias: str) -> str:
    """Hash the executable recipe, excluding presentation-only fields."""

    return _sha256(
        {
            "result_alias": result_alias,
            "steps": [canonical_content(step) for step in steps],
        }
    )


def execution_key(
    plan: AnalysisPlan,
    *,
    result_alias: str,
    engine: str | None = None,
) -> str:
    """Return the tenant-scoped cache key for one native execution."""

    if engine is None:
        # Imported here so building a key does not require the engine to be
        # importable in a process that never executes anything.
        from .native.engine import engine_version

        engine = engine_version()
    signatures = [
        dataset_content_signature(dataset)
        for dataset in sorted(plan.input_datasets, key=lambda item: item.alias)
    ]
    return _sha256(
        {
            "execution_key_version": EXECUTION_KEY_VERSION,
            "user_id": plan.user_id,
            "workspace_id": plan.workspace_id,
            "input_signatures": signatures,
            "recipe_hash": native_recipe_hash(plan.steps, result_alias),
            "engine_version": engine,
            "semantics": semantics.semantics_fingerprint(),
        }
    )


def _sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "EXECUTION_KEY_VERSION",
    "dataset_content_signature",
    "execution_key",
    "native_recipe_hash",
]
