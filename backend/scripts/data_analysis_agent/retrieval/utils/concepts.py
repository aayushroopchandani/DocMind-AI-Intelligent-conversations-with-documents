from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_COMPOUND_ACRONYM_RE = re.compile(
    r"\b[A-Z]{1,6}(?:\s*[&/.-]\s*[A-Z]{1,6})+\b"
)
_UPPER_ACRONYM_RE = re.compile(r"\b[A-Z]{2,8}\b")
_ACRONYM_CONNECTORS = frozenset({"a", "an", "and", "of", "the"})


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = (
        text.replace("_", " ")
        .replace("&", " and ")
        .replace("₹", " inr ")
        .replace("$", " usd ")
        .replace("€", " eur ")
        .replace("£", " gbp ")
        .replace("%", " percent ")
    )
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return _SPACE_RE.sub(" ", text).strip()


def _raw_tokens(value: Any) -> list[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("_", " ")
    return _WORD_RE.findall(text)


def _token_root(token: str) -> str:
    normalized = token.casefold()
    if len(normalized) > 4 and normalized.endswith("ies"):
        return normalized[:-3] + "y"
    if len(normalized) > 4 and normalized.endswith("s") and not normalized.endswith("ss"):
        return normalized[:-1]
    return normalized


def _tokens_equal(query_token: str, candidate_token: str) -> bool:
    if len(query_token) == 1 and query_token.isalpha() and query_token.isupper():
        return query_token == candidate_token
    return _token_root(query_token) == _token_root(candidate_token)


def _contains_contiguous_tokens(query: Sequence[str], candidate: Sequence[str]) -> bool:
    if not query or len(query) > len(candidate):
        return False
    return any(
        all(
            _tokens_equal(query_token, candidate[start + offset])
            for offset, query_token in enumerate(query)
        )
        for start in range(len(candidate) - len(query) + 1)
    )


def _contains_ordered_near_tokens(
    query: Sequence[str],
    candidate: Sequence[str],
    *,
    extra_window: int = 6,
) -> bool:
    if len(query) < 2 or len(query) > len(candidate):
        return False
    maximum_span = len(query) + extra_window
    for start, candidate_token in enumerate(candidate):
        if not _tokens_equal(query[0], candidate_token):
            continue
        query_offset = 1
        end_limit = min(len(candidate), start + maximum_span)
        for position in range(start + 1, end_limit):
            if _tokens_equal(query[query_offset], candidate[position]):
                query_offset += 1
                if query_offset == len(query):
                    return True
    return False


def _explicit_acronyms(value: Any) -> set[str]:
    text = unicodedata.normalize("NFKC", str(value or ""))
    acronyms = {
        "".join(character for character in match.group() if character.isalpha()).casefold()
        for match in _COMPOUND_ACRONYM_RE.finditer(text)
    }
    acronyms.update(match.group().casefold() for match in _UPPER_ACRONYM_RE.finditer(text))
    return {acronym for acronym in acronyms if 2 <= len(acronym) <= 8}


def _contains_initial_expansion(acronym: str, value: Any) -> bool:
    tokens = [
        _token_root(token)
        for token in _raw_tokens(value)
        if token.casefold() not in _ACRONYM_CONNECTORS
    ]
    size = len(acronym)
    if size > len(tokens):
        return False
    return any(
        "".join(token[0] for token in tokens[start : start + size]) == acronym
        for start in range(len(tokens) - size + 1)
    )


def acronym_matches(left: Any, right: Any) -> bool:
    return any(
        _contains_initial_expansion(acronym, right)
        for acronym in _explicit_acronyms(left)
    ) or any(
        _contains_initial_expansion(acronym, left)
        for acronym in _explicit_acronyms(right)
    )


def phrase_match_strength(phrase: Any, candidate_text: Any) -> float:
    query_tokens = _raw_tokens(phrase)
    candidate_tokens = _raw_tokens(candidate_text)
    if not query_tokens or not candidate_tokens:
        return 0.0
    if _contains_contiguous_tokens(query_tokens, candidate_tokens):
        return 1.0
    if acronym_matches(phrase, candidate_text):
        return 0.95
    if _contains_ordered_near_tokens(query_tokens, candidate_tokens):
        return 0.82
    return 0.0


@dataclass(frozen=True, slots=True)
class RetrievalConcept:
    canonical: str
    variants: tuple[str, ...] = ()

    @property
    def terms(self) -> tuple[str, ...]:
        return (self.canonical, *self.variants)

    def match_strength(self, candidate_text: Any) -> float:
        return max(
            (
                phrase_match_strength(term, candidate_text)
                for term in self.terms
            ),
            default=0.0,
        )


def _clean_term(value: Any) -> str:
    return " ".join(str(value or "").split()).strip(" .,:;")


def parse_concepts(
    values: Sequence[Mapping[str, Any]],
    *,
    fallback_terms: Sequence[Any] = (),
) -> tuple[RetrievalConcept, ...]:
    concepts: list[RetrievalConcept] = []
    covered_terms: set[str] = set()
    for value in values:
        canonical = _clean_term(value.get("canonical"))
        if not canonical:
            continue
        variants: list[str] = []
        seen = {normalize_text(canonical)}
        for raw_variant in value.get("variants") or []:
            variant = _clean_term(raw_variant)
            normalized = normalize_text(variant)
            if variant and normalized not in seen:
                variants.append(variant)
                seen.add(normalized)
        concepts.append(RetrievalConcept(canonical, tuple(variants)))
        covered_terms.update(seen)

    for raw_term in fallback_terms:
        term = _clean_term(raw_term)
        normalized = normalize_text(term)
        if term and normalized not in covered_terms:
            concepts.append(RetrievalConcept(term))
            covered_terms.add(normalized)
    return tuple(concepts)


def concept_specificities(
    concepts: Sequence[RetrievalConcept],
    candidate_texts: Sequence[str],
) -> tuple[float, ...]:
    """Estimate signal value from candidate-set prevalence without a term list."""

    candidate_count = len(candidate_texts)
    if not concepts:
        return ()
    if candidate_count <= 1:
        return tuple(1.0 for _ in concepts)

    weights: list[float] = []
    for concept in concepts:
        document_frequency = sum(
            concept.match_strength(text) > 0 for text in candidate_texts
        )
        rarity = (candidate_count - document_frequency) / (candidate_count - 1)
        weights.append(0.25 + (0.75 * rarity))
    return tuple(weights)


def concept_coverage(
    concepts: Sequence[RetrievalConcept],
    candidate_text: Any,
    *,
    specificities: Sequence[float],
) -> float:
    if not concepts:
        return 0.0
    if len(concepts) != len(specificities):
        raise ValueError("Every retrieval concept requires one specificity weight")
    score = sum(
        specificity * concept.match_strength(candidate_text)
        for concept, specificity in zip(concepts, specificities, strict=True)
    )
    return min(1.0, score / len(concepts))
