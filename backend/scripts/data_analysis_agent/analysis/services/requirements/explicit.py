from __future__ import annotations

import re
from dataclasses import dataclass

from ...models.requirements import normalize_requirement_text


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_IGNORED_GROUNDING_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "amount",
        "did",
        "for",
        "how",
        "in",
        "many",
        "number",
        "of",
        "or",
        "the",
        "to",
        "value",
        "with",
    }
)
_CATEGORY_LABELS = {
    "categories": "category",
    "category": "category",
    "countries": "country",
    "country": "country",
    "customers": "customer",
    "customer": "customer",
    "departments": "department",
    "department": "department",
    "genders": "gender",
    "gender": "gender",
    "grades": "grade",
    "grade": "grade",
    "groups": "group",
    "group": "group",
    "products": "product",
    "product": "product",
    "programs": "program",
    "program": "program",
    "regions": "region",
    "region": "region",
    "schools": "school",
    "school": "school",
    "segments": "segment",
    "segment": "segment",
}
_CATEGORY_LABEL_PATTERN = "|".join(
    sorted((re.escape(value) for value in _CATEGORY_LABELS), key=len, reverse=True)
)
_CATEGORY_COMMA_LIST_RE = re.compile(
    rf"\b(?P<label>{_CATEGORY_LABEL_PATTERN})\s+"
    r"(?P<values>[A-Za-z0-9][A-Za-z0-9&./-]*"
    r"(?:\s*,\s*(?:(?:and|or)\s+)?"
    r"[A-Za-z0-9][A-Za-z0-9&./-]*){2,})",
    re.IGNORECASE,
)
_CATEGORY_PAIR_RE = re.compile(
    rf"\b(?P<label>{_CATEGORY_LABEL_PATTERN})\s+"
    r"(?P<first>[A-Za-z0-9][A-Za-z0-9&./-]*)\s+"
    r"(?:and|or)\s+"
    r"(?P<second>[A-Za-z0-9][A-Za-z0-9&./-]*)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ExplicitCategoryConstraint:
    name: str
    values: tuple[str, ...]


def _singular_token(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def _semantic_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        _singular_token(token)
        for token in _TOKEN_RE.findall(value.casefold().replace("&", " and "))
        if token not in _IGNORED_GROUNDING_TOKENS
    )


def _token_matches(left: str, right: str) -> bool:
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= 4 and longer.startswith(shorter)


def semantically_grounded_in_query(query: str, value: str) -> bool:
    """Return true when every substantive concept token is explicit in the query."""

    required = _semantic_tokens(value)
    available = _semantic_tokens(query)
    if not required or not available:
        return False
    matched = sum(
        any(_token_matches(token, query_token) for query_token in available)
        for token in required
    )
    minimum = 1 if len(required) == 1 else max(2, (4 * len(required) + 4) // 5)
    return matched >= minimum


def _clean_category_values(values: str) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for value in re.split(r"\s*,\s*(?:(?:and|or)\s+)?", values):
        cleaned = normalize_requirement_text(value)
        canonical = cleaned.casefold()
        if cleaned and canonical not in seen:
            seen.add(canonical)
            output.append(cleaned)
    return tuple(output)


def explicit_category_constraints(
    query: str,
) -> tuple[ExplicitCategoryConstraint, ...]:
    """Recover small, explicit category lists such as Customers A, B, and C."""

    output: list[ExplicitCategoryConstraint] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for match in _CATEGORY_COMMA_LIST_RE.finditer(query):
        name = _CATEGORY_LABELS[match.group("label").casefold()]
        values = _clean_category_values(match.group("values"))
        key = (name, tuple(value.casefold() for value in values))
        if len(values) >= 3 and key not in seen:
            seen.add(key)
            output.append(ExplicitCategoryConstraint(name=name, values=values))
    for match in _CATEGORY_PAIR_RE.finditer(query):
        name = _CATEGORY_LABELS[match.group("label").casefold()]
        values = (
            normalize_requirement_text(match.group("first")),
            normalize_requirement_text(match.group("second")),
        )
        key = (name, tuple(value.casefold() for value in values))
        if key not in seen:
            seen.add(key)
            output.append(ExplicitCategoryConstraint(name=name, values=values))
    return tuple(output)
