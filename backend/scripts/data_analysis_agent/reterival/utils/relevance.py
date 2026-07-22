from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .concepts import (
    RetrievalConcept,
    concept_coverage,
    concept_specificities,
    normalize_text,
    parse_concepts,
    phrase_match_strength,
)


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
    concepts: tuple[RetrievalConcept, ...] = ()
    years: tuple[str, ...] = ()
    units: tuple[str, ...] = ()

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> "RetrievalSignals":
        query = str(state.get("query") or "")
        raw_concepts = [
            value
            for value in state.get("match_concepts", [])
            if isinstance(value, Mapping)
        ]
        return cls(
            concepts=parse_concepts(
                raw_concepts,
                fallback_terms=[
                    *state.get("metrics", []),
                    *state.get("entities", []),
                ],
            ),
            years=_clean_periods(
                [*state.get("years", []), *_explicit_periods(query)]
            ),
            units=_clean_signals(
                [*state.get("units", []), *_explicit_units(query)]
            ),
        )


def _signal_coverage(signals: Sequence[str], candidate_text: str) -> float:
    if not signals:
        return 0.0
    return sum(
        phrase_match_strength(signal, candidate_text) for signal in signals
    ) / len(signals)


def _rrf_score(candidate: Mapping[str, Any], query_count: int) -> float:
    theoretical_max = (2 * max(1, query_count)) / 61
    raw_score = max(0.0, float(candidate.get("rrf_score") or 0.0))
    return min(1.0, raw_score / theoretical_max)


def _raw_consensus(candidate: Mapping[str, Any], query_count: int) -> float:
    modes = {
        str(mode).casefold()
        for mode in candidate.get("retrieval_modes", [])
        if str(mode).casefold() in {"dense", "sparse"}
    }
    matched_queries = {
        normalized
        for query in candidate.get("matched_queries", [])
        if (normalized := normalize_text(query))
    }
    mode_score = len(modes) / 2
    query_score = min(1.0, len(matched_queries) / max(1, query_count))
    return (0.35 * mode_score) + (0.65 * query_score)


@dataclass(frozen=True, slots=True)
class ScoringContext:
    signals: RetrievalSignals
    query_count: int
    concept_specificities: tuple[float, ...]
    consensus_scores: tuple[float, ...]

    def consensus(self, candidate: Mapping[str, Any]) -> float:
        candidate_count = len(self.consensus_scores)
        if candidate_count <= 1:
            return 0.0
        raw_score = _raw_consensus(candidate, self.query_count)
        lower_count = sum(
            score < raw_score - 1e-9 for score in self.consensus_scores
        )
        tied_count = sum(
            abs(score - raw_score) <= 1e-9 for score in self.consensus_scores
        )
        percentile = lower_count / (candidate_count - 1)
        tie_penalty = (candidate_count - tied_count + 1) / candidate_count
        return percentile * tie_penalty


def build_scoring_context(
    candidates: Sequence[Mapping[str, Any]],
    *,
    signals: RetrievalSignals,
    query_count: int,
    candidate_text: Callable[[Mapping[str, Any]], str],
) -> ScoringContext:
    candidate_texts = [candidate_text(candidate) for candidate in candidates]
    consensus_scores = [
        _raw_consensus(candidate, query_count) for candidate in candidates
    ]
    return ScoringContext(
        signals=signals,
        query_count=query_count,
        concept_specificities=concept_specificities(
            signals.concepts,
            candidate_texts,
        ),
        consensus_scores=tuple(consensus_scores),
    )


def text_candidate_content(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("text") or "")


def table_candidate_content(candidate: Mapping[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            candidate.get("title"),
            candidate.get("summary"),
            *(candidate.get("columns") or []),
            *(candidate.get("metrics") or []),
            *(candidate.get("units") or []),
            *(candidate.get("keywords") or []),
        )
    )


def _table_schema_content(candidate: Mapping[str, Any]) -> str:
    return " ".join(
        str(value or "") for value in candidate.get("columns", [])
    )


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
    context: ScoringContext,
) -> dict[str, Any]:
    text = text_candidate_content(candidate)
    features = {
        "rrf": _rrf_score(candidate, context.query_count),
        "concept": concept_coverage(
            context.signals.concepts,
            text,
            specificities=context.concept_specificities,
        ),
        "year": _signal_coverage(context.signals.years, text),
        "unit": _signal_coverage(context.signals.units, text),
        "consensus": context.consensus(candidate),
    }
    score = (
        (0.45 * features["rrf"])
        + (0.40 * features["concept"])
        + (0.06 * features["year"])
        + (0.02 * features["unit"])
        + (0.07 * features["consensus"])
    )
    return _with_score(candidate, score=score, features=features)


def score_table_candidate(
    candidate: Mapping[str, Any],
    *,
    context: ScoringContext,
) -> dict[str, Any]:
    combined_text = table_candidate_content(candidate)
    features = {
        "rrf": _rrf_score(candidate, context.query_count),
        "concept": concept_coverage(
            context.signals.concepts,
            combined_text,
            specificities=context.concept_specificities,
        ),
        "schema": concept_coverage(
            context.signals.concepts,
            _table_schema_content(candidate),
            specificities=context.concept_specificities,
        ),
        "year": _signal_coverage(context.signals.years, combined_text),
        "unit": _signal_coverage(context.signals.units, combined_text),
        "consensus": context.consensus(candidate),
    }
    score = (
        (0.36 * features["rrf"])
        + (0.36 * features["concept"])
        + (0.16 * features["schema"])
        + (0.05 * features["year"])
        + (0.02 * features["unit"])
        + (0.05 * features["consensus"])
    )
    return _with_score(candidate, score=score, features=features)
