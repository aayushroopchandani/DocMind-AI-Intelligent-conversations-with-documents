from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_SPACE_RE = re.compile(r"\s+")
_PERIOD_RE = re.compile(
    r"\b(?:fy\s*)?(?:19|20)\d{2}"
    r"(?:\s*[-–/]\s*(?:\d{2}|(?:19|20)\d{2}))?\b",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(
    r"(?:₹|\$|€|£|%|\b(?:inr|usd|eur|gbp|crores?|lakhs?|millions?|"
    r"billions?|thousands?|percent(?:age)?)\b)",
    re.IGNORECASE,
)


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = (
        text.replace("_", " ")
        .replace("₹", " inr ")
        .replace("$", " usd ")
        .replace("€", " eur ")
        .replace("£", " gbp ")
        .replace("%", " percent ")
    )
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return _SPACE_RE.sub(" ", text).strip()


def _clean_signals(values: Sequence[Any]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        signal = " ".join(str(value or "").split()).strip(" .,:;")
        canonical = normalize_text(signal)
        if canonical and canonical not in seen:
            seen.add(canonical)
            output.append(signal)
    return tuple(output)


def _clean_periods(values: Sequence[Any]) -> tuple[str, ...]:
    periods = (
        re.sub(
            r"\bfy\s*(?=(?:19|20)\d{2})",
            "",
            str(value or ""),
            flags=re.IGNORECASE,
        )
        for value in values
    )
    return _clean_signals(list(periods))


def _explicit_periods(query: str) -> tuple[str, ...]:
    return _clean_periods(_PERIOD_RE.findall(query))


def _explicit_units(query: str) -> tuple[str, ...]:
    return _clean_signals(_UNIT_RE.findall(query))


@dataclass(frozen=True, slots=True)
class RetrievalSignals:
    metrics: tuple[str, ...] = ()
    years: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    column_terms: tuple[str, ...] = ()

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> "RetrievalSignals":
        query = str(state.get("query") or "")
        return cls(
            metrics=_clean_signals(state.get("metrics", [])),
            years=_clean_periods(
                [*state.get("years", []), *_explicit_periods(query)]
            ),
            entities=_clean_signals(state.get("entities", [])),
            units=_clean_signals(
                [*state.get("units", []), *_explicit_units(query)]
            ),
            column_terms=_clean_signals(state.get("column_terms", [])),
        )


def _coverage(signals: Sequence[str], candidate_text: str) -> float:
    if not signals:
        return 0.0
    normalized = f" {normalize_text(candidate_text)} "
    normalized_signals = [normalize_text(signal) for signal in signals]
    matches = sum(f" {signal} " in normalized for signal in normalized_signals)
    return matches / len(signals)


def _rrf_score(candidate: Mapping[str, Any], query_count: int) -> float:
    theoretical_max = (2 * max(1, query_count)) / 61
    raw_score = max(0.0, float(candidate.get("rrf_score") or 0.0))
    return min(1.0, raw_score / theoretical_max)


def _consensus(candidate: Mapping[str, Any], query_count: int) -> float:
    modes = {
        str(mode).casefold()
        for mode in candidate.get("retrieval_modes", [])
        if str(mode).casefold() in {"dense", "sparse"}
    }
    matched_queries = {
        normalize_text(query)
        for query in candidate.get("matched_queries", [])
        if normalize_text(query)
    }
    mode_score = len(modes) / 2
    query_score = min(1.0, len(matched_queries) / min(3, max(1, query_count)))
    return (mode_score + query_score) / 2


def _with_score(
    candidate: Mapping[str, Any],
    *,
    score: float,
    features: Mapping[str, float],
) -> dict[str, Any]:
    output = dict(candidate)
    output["relevance_score"] = round(score, 6)
    output["relevance_features"] = {
        key: round(value, 6) for key, value in features.items()
    }
    return output


def score_text_candidate(
    candidate: Mapping[str, Any],
    *,
    signals: RetrievalSignals,
    query_count: int,
) -> dict[str, Any]:
    text = str(candidate.get("text") or "")
    features = {
        "rrf": _rrf_score(candidate, query_count),
        "metric_or_column": _coverage(
            (*signals.metrics, *signals.column_terms), text
        ),
        "year": _coverage(signals.years, text),
        "entity": _coverage(signals.entities, text),
        "unit": _coverage(signals.units, text),
        "consensus": _consensus(candidate, query_count),
    }
    score = (
        (0.45 * features["rrf"])
        + (0.20 * features["metric_or_column"])
        + (0.10 * features["year"])
        + (0.10 * features["entity"])
        + (0.05 * features["unit"])
        + (0.10 * features["consensus"])
    )
    return _with_score(candidate, score=score, features=features)


def score_table_candidate(
    candidate: Mapping[str, Any],
    *,
    signals: RetrievalSignals,
    query_count: int,
) -> dict[str, Any]:
    descriptive_text = " ".join(
        str(value or "")
        for value in (
            candidate.get("title"),
            candidate.get("summary"),
            *(candidate.get("metrics") or []),
            *(candidate.get("keywords") or []),
        )
    )
    column_text = " ".join(
        str(value or "") for value in candidate.get("columns", [])
    )
    unit_text = " ".join(
        str(value or "") for value in candidate.get("units", [])
    )
    combined_text = f"{descriptive_text} {column_text} {unit_text}"
    features = {
        "rrf": _rrf_score(candidate, query_count),
        "metric": _coverage(signals.metrics, descriptive_text),
        "column": _coverage(signals.column_terms, column_text),
        "year": _coverage(signals.years, combined_text),
        "entity": _coverage(signals.entities, combined_text),
        "unit": _coverage(signals.units, unit_text or combined_text),
        "consensus": _consensus(candidate, query_count),
    }
    score = (
        (0.35 * features["rrf"])
        + (0.20 * features["metric"])
        + (0.15 * features["column"])
        + (0.10 * features["year"])
        + (0.05 * features["entity"])
        + (0.05 * features["unit"])
        + (0.10 * features["consensus"])
    )
    return _with_score(candidate, score=score, features=features)
