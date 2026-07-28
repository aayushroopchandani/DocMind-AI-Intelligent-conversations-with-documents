from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ...models import (
    ProposedEvidenceFact,
    RequirementKind,
    TextExtractionResponse,
)

if TYPE_CHECKING:
    from .extractor import TextExtractionTask


_LEADING_VALUE_RE = re.compile(
    r"^\s*(?P<raw>[-+]?[$€£₹]?\s*\d+(?:[.,]\d+)*"
    r"(?:\s+(?:thousand|million|billion|crore|lakh)s?)?)\b",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_AREA_RE = re.compile(
    r"\b(acres?|hectares?|square\s+(?:feet|foot|meters?|kilometers?|miles?))\b",
    re.IGNORECASE,
)
_IGNORED_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "count",
        "number",
        "of",
        "or",
        "the",
        "to",
        "under",
    }
)


def _singular(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def _tokens(value: object) -> frozenset[str]:
    return frozenset(
        _singular(token)
        for token in _TOKEN_RE.findall(str(value or "").casefold())
        if token not in _IGNORED_TOKENS
    )


def _explicit_unit(context: str, raw_value: str) -> str | None:
    area = _AREA_RE.search(context)
    if area is not None:
        return area.group(1).casefold()
    if "%" in context:
        return "percent"
    if "$" in raw_value:
        return "USD"
    if "€" in raw_value:
        return "EUR"
    if "£" in raw_value:
        return "GBP"
    if "₹" in raw_value:
        return "INR"
    return None


def _single_required_period(task: TextExtractionTask) -> str | None:
    periods = tuple(
        item.name
        for item in task.requirements
        if item.required and item.kind == RequirementKind.PERIOD
    )
    if len(periods) != 1:
        return None
    period = periods[0]
    document_text = " ".join(
        (
            *(chunk.document_name for chunk in task.chunks),
            *(chunk.text for chunk in task.chunks),
        )
    )
    return period if period.casefold() in document_text.casefold() else None


def extract_labeled_numeric_facts(
    task: TextExtractionTask,
) -> TextExtractionResponse:
    """Recover only explicit `number + label` rows from report text."""

    targets = tuple(
        item
        for item in task.requirements
        if item.requirement_id in task.target_requirement_ids
        and item.kind == RequirementKind.METRIC
    )
    if not targets:
        return TextExtractionResponse(status="absent")
    period = _single_required_period(task)
    facts: list[ProposedEvidenceFact] = []
    seen: set[tuple[str, str, str]] = set()
    for chunk in task.chunks:
        lines = chunk.text.splitlines()
        for index, line in enumerate(lines):
            value_match = _LEADING_VALUE_RE.match(line)
            if value_match is None:
                continue
            raw_value = value_match.group("raw").strip()
            context_lines = [line.strip()]
            if (
                index + 1 < len(lines)
                and _LEADING_VALUE_RE.match(lines[index + 1]) is None
            ):
                context_lines.append(lines[index + 1].strip())
            source_span = "\n".join(
                value for value in context_lines if value
            ).strip()
            context_tokens = _tokens(source_span)
            if not source_span or not context_tokens:
                continue
            for requirement in targets:
                matched_metric = next(
                    (
                        term
                        for term in (
                            requirement.name,
                            *requirement.aliases,
                        )
                        if (
                            (required_tokens := _tokens(term))
                            and required_tokens <= context_tokens
                        )
                    ),
                    None,
                )
                if matched_metric is None:
                    continue
                identity = (
                    requirement.requirement_id,
                    chunk.chunk_id,
                    raw_value.casefold(),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                facts.append(
                    ProposedEvidenceFact(
                        requirement_id=requirement.requirement_id,
                        entity=(
                            requirement.entity_names[0]
                            if requirement.entity_names
                            else chunk.document_name or "source document"
                        ),
                        metric=requirement.name,
                        raw_value=raw_value,
                        unit=_explicit_unit(source_span, raw_value),
                        period=period,
                        document_id=chunk.document_id,
                        chunk_id=chunk.chunk_id,
                        source_span=source_span,
                        confidence=0.90,
                    )
                )
                if len(facts) == 30:
                    return TextExtractionResponse(
                        status="evidence",
                        facts=tuple(facts),
                    )
    return TextExtractionResponse(
        status="evidence" if facts else "absent",
        facts=tuple(facts),
    )
