from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from ...models import (
    DerivedDatasetColumn,
    DerivedDatasetReference,
    EvidenceFact,
)
from ...repositories import DerivedDatasetWrite
from ..assessment.rules import normalized_phrase


def _group_key(fact: EvidenceFact) -> tuple[Any, ...]:
    return (
        fact.document_id,
        normalized_phrase(fact.entity),
        normalized_phrase(fact.metric),
        normalized_phrase(fact.unit),
        tuple(
            sorted(
                (normalized_phrase(item.name), normalized_phrase(item.value))
                for item in fact.dimensions
            )
        ),
    )


def build_derived_dataset_writes(
    facts: tuple[EvidenceFact, ...],
) -> tuple[DerivedDatasetWrite, ...]:
    """Promote coherent three-period series; isolated facts remain state-only."""

    grouped: dict[tuple[Any, ...], list[EvidenceFact]] = defaultdict(list)
    for fact in facts:
        if fact.period:
            grouped[_group_key(fact)].append(fact)

    output: list[DerivedDatasetWrite] = []
    for values in grouped.values():
        periods = tuple(
            dict.fromkeys(
                fact.period for fact in values if fact.period is not None
            )
        )
        if len(values) < 3 or len(periods) < 3:
            continue
        ordered = sorted(
            values,
            key=lambda item: (item.period or "", item.fact_id),
        )
        identity = json.dumps(
            {
                "facts": [item.fact_id for item in ordered],
                "chunks": [
                    (item.chunk_id, item.chunk_hash) for item in ordered
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        dataset_id = (
            "derived_"
            + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        )
        first = ordered[0]
        rows = tuple(
            {
                "entity": fact.entity,
                "metric": fact.metric,
                "period": fact.period,
                "value": fact.normalized_value,
                "unit": fact.unit,
                "dimensions": {
                    item.name: item.value for item in fact.dimensions
                },
                "fact_id": fact.fact_id,
            }
            for fact in ordered
        )
        minimum_confidence = min(fact.confidence for fact in ordered)
        reference = DerivedDatasetReference(
            derived_dataset_id=dataset_id,
            document_id=first.document_id,
            title=f"{first.metric} by period",
            summary=(
                f"{first.entity}: {first.metric} for "
                f"{', '.join(periods)}"
                + (f". Unit: {first.unit}." if first.unit else ".")
            ),
            source_chunk_ids=tuple(
                dict.fromkeys(fact.chunk_id for fact in ordered)
            ),
            source_content_hashes=tuple(
                dict.fromkeys(fact.chunk_hash for fact in ordered)
            ),
            requirement_ids=tuple(
                dict.fromkeys(fact.requirement_id for fact in ordered)
            ),
            columns=(
                DerivedDatasetColumn(key="entity", label="Entity", type="string"),
                DerivedDatasetColumn(key="metric", label="Metric", type="string"),
                DerivedDatasetColumn(key="period", label="Period", type="string"),
                DerivedDatasetColumn(key="value", label="Value", type="number"),
                DerivedDatasetColumn(key="unit", label="Unit", type="string"),
            ),
            row_count=len(rows),
            unit=first.unit,
            periods=periods,
            reusability_status=(
                "promotion_candidate"
                if len(rows) >= 4 and minimum_confidence >= 0.90
                else "cached"
            ),
            model=first.model,
        )
        output.append(
            DerivedDatasetWrite(
                reference=reference,
                rows=rows,
                facts=tuple(ordered),
            )
        )
    return tuple(output)
