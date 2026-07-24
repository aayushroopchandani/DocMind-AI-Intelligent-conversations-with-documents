from __future__ import annotations

import re
from pathlib import PurePath

from ...models.requirements import canonical_requirement_text


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "the",
        "to",
    }
)
_SAFE_EQUIVALENCE_GROUPS = (
    frozenset({"research and development", "r d", "r and d"}),
    frozenset({"selling general and administrative", "sg a", "s g and a"}),
    frozenset({"earnings per share", "eps"}),
    frozenset({"income from operations", "operating income"}),
    frozenset({"fiscal year", "fy"}),
    frozenset({"year over year", "yoy"}),
)


def normalized_phrase(value: object) -> str:
    text = canonical_requirement_text(value).replace("&", " and ")
    normalized = " ".join(_TOKEN_RE.findall(text))
    normalized = re.sub(
        r"\br and d\b",
        "research and development",
        normalized,
    )
    normalized = re.sub(
        r"\bsg and a\b",
        "selling general and administrative",
        normalized,
    )
    return normalized


def phrase_tokens(value: object) -> frozenset[str]:
    return frozenset(
        token
        for token in _TOKEN_RE.findall(normalized_phrase(value))
        if token not in _STOP_WORDS
    )


def acronym(value: object) -> str:
    tokens = tuple(phrase_tokens(value))
    if len(tokens) < 2:
        return ""
    ordered = [
        token
        for token in _TOKEN_RE.findall(normalized_phrase(value))
        if token not in _STOP_WORDS
    ]
    return "".join(token[0] for token in ordered)


def safe_equivalent(left: object, right: object) -> bool:
    left_value = normalized_phrase(left)
    right_value = normalized_phrase(right)
    if not left_value or not right_value:
        return False
    if left_value == right_value:
        return True
    if acronym(left_value) == right_value.replace(" ", ""):
        return True
    if acronym(right_value) == left_value.replace(" ", ""):
        return True
    return any(
        left_value in group and right_value in group
        for group in _SAFE_EQUIVALENCE_GROUPS
    )


def lexical_score(requirement: object, candidate: object) -> float:
    """Conservative label similarity; related terms are not treated as aliases."""

    required = normalized_phrase(requirement)
    available = normalized_phrase(candidate)
    if not required or not available:
        return 0.0
    if required == available:
        return 1.0
    if safe_equivalent(required, available):
        return 0.94
    required_tokens = phrase_tokens(required)
    available_tokens = phrase_tokens(available)
    if not required_tokens or not available_tokens:
        return 0.0
    intersection = len(required_tokens & available_tokens)
    if not intersection:
        return 0.0
    containment = intersection / len(required_tokens)
    jaccard = intersection / len(required_tokens | available_tokens)
    if containment == 1.0:
        return min(0.89, 0.76 + (0.13 * jaccard))
    return min(0.79, (0.60 * containment) + (0.30 * jaccard))


def document_display_name(value: str) -> str:
    name = PurePath(value).name
    lowered = name.casefold()
    for suffix in (".pdf", ".docx", ".xlsx", ".xls", ".csv"):
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return name


def contains_phrase(text: object, phrase: object) -> bool:
    normalized_text = normalized_phrase(text)
    normalized_target = normalized_phrase(phrase)
    if not normalized_text or not normalized_target:
        return False
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_target)}(?![a-z0-9])",
            normalized_text,
        )
    )
