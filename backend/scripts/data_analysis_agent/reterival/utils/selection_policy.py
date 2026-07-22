from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    text_minimum_score: float
    text_relative_ratio: float
    table_minimum_score: float
    table_relative_ratio: float
    table_minimum_substantive_match: float


def selection_policy(*, scope: str, table_intent: str) -> SelectionPolicy:
    if scope not in {"normal", "broad"}:
        raise ValueError(f"Unsupported retrieval scope: {scope!r}")
    if table_intent not in {"required", "supporting", "none"}:
        raise ValueError(f"Unsupported table intent: {table_intent!r}")

    if scope == "broad":
        text_score, text_ratio = 0.28, 0.42
        table_score, table_ratio = 0.36, 0.48
        substantive_match = 0.08
    else:
        text_score, text_ratio = 0.38, 0.75
        if table_intent == "required":
            table_score, table_ratio = 0.42, 0.72
            substantive_match = 0.14
        else:
            table_score, table_ratio = 0.48, 0.75
            substantive_match = 0.18

    return SelectionPolicy(
        text_minimum_score=text_score,
        text_relative_ratio=text_ratio,
        table_minimum_score=table_score,
        table_relative_ratio=table_ratio,
        table_minimum_substantive_match=substantive_match,
    )
