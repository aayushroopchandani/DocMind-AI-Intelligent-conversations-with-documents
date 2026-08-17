"""Input-to-output lineage and the canonical replay recipe (Phase 9.9.1).

The lineage document answers two questions a result has to be able to answer
about itself: *where did this come from*, and *how would I get it again*. The
second is the more demanding one — it means the document has to carry enough to
reproduce the result exactly, which is why it records the canonical recipe hash
alongside every version that could change the answer.

It is a blob, not a MongoDB field. A recipe with sixty steps and thirty inputs
is not bounded metadata (9.9.4).
"""

from __future__ import annotations

from typing import Any

from ....runtime.models.canonical import canonical_content
from ....runtime.models.plans import AnalysisPlan, PlanColumn
from ..contracts import NativeRecipe, StepMetrics
from ..idempotency import dataset_content_signature


LINEAGE_FORMAT_VERSION = "1.0"


def build_lineage(
    *,
    plan: AnalysisPlan,
    recipe: NativeRecipe,
    execution_key: str,
    recipe_hash: str,
    content_hash: str,
    result_columns: tuple[PlanColumn, ...],
    step_metrics: tuple[StepMetrics, ...],
) -> dict[str, Any]:
    """Return the durable lineage document for one execution."""

    return {
        "format_version": LINEAGE_FORMAT_VERSION,
        "identity": {
            "execution_key": execution_key,
            "run_id": plan.run_id,
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "workspace_id": plan.workspace_id,
        },
        "versions": {
            "plan_schema": plan.plan_version,
            "capability_profile": plan.capability_profile,
            "capability_version": plan.capability_version,
            "canonicalizer": plan.canonicalizer_version,
            "validator": plan.validator_version,
            "engine": recipe.engine_version,
            "semantics": recipe.semantics_version,
            "recipe": recipe.recipe_version,
        },
        "inputs": [
            {
                "alias": dataset.alias,
                "dataset_id": dataset.dataset_id,
                "dataset_version": dataset.dataset_version,
                "content_signature": dataset_content_signature(dataset),
                "row_count": dataset.row_count,
                "columns": [column.key for column in dataset.columns],
                "sources": [
                    {
                        "source_dataset_id": item.source_dataset_id,
                        "source_version": item.source_version,
                        "source_type": item.source_type.value,
                    }
                    for item in dataset.provenance
                ],
            }
            for dataset in plan.input_datasets
        ],
        "output": {
            "content_hash": content_hash,
            "result_alias": recipe.result_alias,
            "columns": [
                {
                    "key": column.key,
                    "data_type": column.data_type.value,
                    "unit": column.unit,
                }
                for column in result_columns
            ],
        },
        "steps": [
            {
                "step_id": metric.step_id,
                "kind": metric.kind,
                "input_rows": metric.input_rows,
                "output_rows": metric.output_rows,
                "removed_rows": metric.removed_rows,
                "output_columns": metric.output_columns,
            }
            for metric in step_metrics
        ],
        # The recipe itself, canonicalized the same way the plan hash is, so a
        # replay can be proven identical rather than merely similar.
        "replay": {
            "recipe_hash": recipe_hash,
            "steps": [canonical_content(step) for step in recipe.steps],
        },
    }


__all__ = ["LINEAGE_FORMAT_VERSION", "build_lineage"]
