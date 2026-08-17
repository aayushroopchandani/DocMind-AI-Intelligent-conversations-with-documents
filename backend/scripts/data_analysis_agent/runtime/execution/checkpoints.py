"""Which stages are worth checkpointing, and how a checkpoint is keyed (9.8.3).

The plan is explicit that this must be selective: "do not upload a checkpoint
after every tiny filter or rename. That would add more storage and latency than
it saves." So the policy is a cost judgement, not a rule that every stage gets
one.

A stage earns a checkpoint when redoing it would cost more than storing it:

* it is a materialization barrier the engine had to collect anyway (pivot,
  generation), so the bytes already exist in memory;
* it is a join or aggregate above a size threshold, where recomputation is
  genuinely expensive;
* it fans out to several downstream stages, so one store saves many recomputes;
* it is the final result, which is published regardless.

Everything else — filters, renames, selects, sorts on small inputs — is cheaper
to recompute than to upload.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..models.canonical import canonical_content
from ..models.plans import PlanStep, step_input_aliases


# Below this, recomputation is cheaper than a round trip to blob storage.
CHECKPOINT_ROW_THRESHOLD = 50_000

_EXPENSIVE_KINDS = frozenset({"join", "pivot", "aggregate", "generate_dataset"})
_ALWAYS_MATERIALIZED = frozenset({"pivot", "generate_dataset"})


@dataclass(frozen=True, slots=True)
class CheckpointDecision:
    should_store: bool
    reason: str


def stage_recipe_hash(steps: tuple[PlanStep, ...]) -> str:
    """Hash the canonical steps a stage runs.

    Used as part of a checkpoint's key so a checkpoint is only reused for the
    exact recipe that produced it.
    """

    payload = [canonical_content(step) for step in steps]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def consumer_counts(steps: tuple[PlanStep, ...]) -> dict[str, int]:
    """Return how many steps read each alias."""

    counts: dict[str, int] = {}
    for step in steps:
        for alias in step_input_aliases(step):
            counts[alias] = counts.get(alias, 0) + 1
    return counts


def decide(
    step: PlanStep,
    *,
    output_rows: int,
    consumers: int,
    is_final: bool,
) -> CheckpointDecision:
    """Return whether this stage's output is worth storing."""

    if is_final:
        return CheckpointDecision(True, "final_result")
    if step.kind in _ALWAYS_MATERIALIZED:
        # The engine already collected this, so storing it costs only the upload.
        return CheckpointDecision(True, "materialization_barrier")
    if consumers > 1:
        return CheckpointDecision(True, "fan_out_branch")
    if step.kind in _EXPENSIVE_KINDS and output_rows >= CHECKPOINT_ROW_THRESHOLD:
        return CheckpointDecision(True, "expensive_large_stage")
    return CheckpointDecision(
        False,
        "cheaper_to_recompute",
    )


def plan_checkpoints(
    steps: tuple[PlanStep, ...],
    *,
    result_alias: str,
    row_counts: dict[str, int],
) -> dict[str, CheckpointDecision]:
    """Return the checkpoint decision for every stage in a compiled recipe."""

    consumers = consumer_counts(steps)
    return {
        step.step_id: decide(
            step,
            output_rows=row_counts.get(step.output_alias, 0),
            consumers=consumers.get(step.output_alias, 0),
            is_final=step.output_alias == result_alias,
        )
        for step in steps
    }


__all__ = [
    "CHECKPOINT_ROW_THRESHOLD",
    "CheckpointDecision",
    "consumer_counts",
    "decide",
    "plan_checkpoints",
    "stage_recipe_hash",
]
