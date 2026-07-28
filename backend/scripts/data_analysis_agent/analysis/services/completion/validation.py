from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ...models import (
    CompletionStage,
    EvidenceFact,
    ProposedEvidenceFact,
    RejectedEvidence,
    RequirementItem,
    RequirementKind,
    TextEvidenceReference,
    TextExtractionResponse,
    stable_fact_id,
)
from ..assessment.rules import contains_phrase, lexical_score, normalized_phrase


_NUMBER_RE = re.compile(
    r"(?P<negative>\()?\s*[-+]?[$€£₹]?\s*"
    r"(?P<number>\d+(?:[.,]\d+)*)"
)
_MAGNITUDE_RE = re.compile(
    r"\b(?P<magnitude>thousand|million|billion|crore|lakh)s?\b",
    re.IGNORECASE,
)
_AREA_UNIT_RE = re.compile(
    r"\b(?:acres?|hectares?|square\s+(?:feet|foot|meters?|kilometers?|miles?))\b",
    re.IGNORECASE,
)
_AREA_METRIC_RE = re.compile(
    r"\b(?:acres?|hectares?|area|land|territor(?:y|ies)|"
    r"square\s+(?:feet|foot|meters?|kilometers?|miles?))\b",
    re.IGNORECASE,
)
_UNIT_ALIASES = {
    "percent": ("percent", "percentage", "%"),
    "usd": ("usd", "dollar", "$"),
    "eur": ("eur", "euro", "€"),
    "gbp": ("gbp", "pound", "£"),
    "inr": ("inr", "rupee", "₹"),
    "thousand": ("thousand", "000"),
    "million": ("million",),
    "billion": ("billion",),
    "crore": ("crore",),
    "lakh": ("lakh",),
}
_SUBSTANTIVE_KINDS = frozenset(
    {
        RequirementKind.METRIC,
        RequirementKind.DIMENSION,
        RequirementKind.FILTER,
        RequirementKind.TOPIC,
    }
)
_METRIC_TOKEN_RE = re.compile(r"[a-z0-9]+")
_METRIC_TOKEN_STOP_WORDS = frozenset(
    {"a", "an", "and", "count", "number", "of", "or", "the", "to", "under"}
)


@dataclass(frozen=True, slots=True)
class TextExtractionValidationResult:
    facts: tuple[EvidenceFact, ...]
    rejected: tuple[RejectedEvidence, ...]


def _normalized_number_token(value: str) -> str:
    comma_count = value.count(",")
    dot_count = value.count(".")
    if comma_count and dot_count:
        decimal_separator = "," if value.rfind(",") > value.rfind(".") else "."
        grouping_separator = "." if decimal_separator == "," else ","
        return value.replace(grouping_separator, "").replace(decimal_separator, ".")
    if comma_count:
        parts = value.split(",")
        if len(parts) == 2 and len(parts[1]) in {1, 2}:
            return ".".join(parts)
        return "".join(parts)
    if dot_count > 1:
        parts = value.split(".")
        if all(len(part) == 3 for part in parts[1:]):
            return "".join(parts)
        return "".join(parts[:-1]) + "." + parts[-1]
    return value


def _normalized_decimal(raw_value: str) -> str | None:
    match = _NUMBER_RE.search(raw_value)
    if match is None:
        return None
    try:
        value = Decimal(_normalized_number_token(match.group("number")))
    except InvalidOperation:
        return None
    if match.group("negative") or raw_value.strip().startswith("-"):
        value = -value
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _normalized_unit(unit: str | None, raw_value: str) -> str | None:
    normalized = " ".join(str(unit or "").split()).strip() or None
    magnitude_match = _MAGNITUDE_RE.search(raw_value)
    if magnitude_match is None:
        return normalized
    magnitude = magnitude_match.group("magnitude").casefold()
    if normalized is None:
        return magnitude
    if contains_phrase(normalized, magnitude):
        return normalized
    currency_prefixes = ("usd", "eur", "gbp", "inr")
    if normalized.casefold() in currency_prefixes:
        return f"{normalized} {magnitude}"
    return f"{magnitude} {normalized}"


def _unit_supported(unit: str | None, text: str) -> bool:
    if not unit:
        return True
    if unit.casefold() in text.casefold():
        return True
    normalized_unit = normalized_phrase(unit)
    normalized_text = normalized_phrase(text)
    if normalized_unit and normalized_unit in normalized_text:
        return True
    tokens = set(normalized_unit.split())
    recognized = False
    for canonical, aliases in _UNIT_ALIASES.items():
        if canonical not in tokens:
            continue
        recognized = True
        if not any(alias in text.casefold() for alias in aliases):
            return False
    return recognized


def _unit_semantically_compatible(
    requirement: RequirementItem,
    unit: str | None,
) -> bool:
    """Reject measurement families that cannot quantify the requested metric."""

    if not unit or not _AREA_UNIT_RE.search(unit):
        return True
    terms = " ".join((requirement.name, *requirement.aliases))
    return bool(_AREA_METRIC_RE.search(terms))


def _measurement_unit_near_value(
    text: str,
    raw_value: str,
) -> str | None:
    occurrences = _raw_value_occurrences(text, raw_value)
    if not occurrences:
        return None
    start = occurrences[0][1]
    nearby = text[start : min(len(text), start + 100)]
    area_match = _AREA_UNIT_RE.search(nearby)
    return area_match.group(0) if area_match is not None else None


def _flexible_span(
    text: str,
    proposed_span: str,
) -> tuple[int, int] | None:
    pieces = tuple(re.findall(r"\S+", proposed_span.strip()))
    if not pieces:
        return None
    pattern = r"\s+".join(re.escape(piece) for piece in pieces)
    match = re.search(pattern, text, re.IGNORECASE)
    return (match.start(), match.end()) if match else None


def _raw_value_occurrences(text: str, raw_value: str) -> tuple[tuple[int, int], ...]:
    if not raw_value.strip():
        return ()
    return tuple(
        (match.start(), match.end())
        for match in re.finditer(re.escape(raw_value.strip()), text, re.IGNORECASE)
    )


def _source_window(
    text: str,
    value_start: int,
    value_end: int,
    *,
    maximum_chars: int = 600,
) -> tuple[int, int]:
    lower_bound = max(0, value_start - maximum_chars // 2)
    upper_bound = min(len(text), value_end + maximum_chars // 2)
    before = text.rfind("\n", lower_bound, value_start)
    sentence_before = max(
        text.rfind(". ", lower_bound, value_start),
        text.rfind("? ", lower_bound, value_start),
        text.rfind("! ", lower_bound, value_start),
    )
    start = max(before, sentence_before + 1)
    start = lower_bound if start < lower_bound else start
    after_candidates = tuple(
        position
        for position in (
            text.find("\n", value_end, upper_bound),
            text.find(". ", value_end, upper_bound),
            text.find("? ", value_end, upper_bound),
            text.find("! ", value_end, upper_bound),
        )
        if position >= 0
    )
    end = min(after_candidates) + 1 if after_candidates else upper_bound
    if end - start > maximum_chars:
        start = max(0, value_start - maximum_chars // 3)
        end = min(len(text), start + maximum_chars)
    return start, end


def _grounded_source_span(
    *,
    chunk_text: str,
    proposed_span: str,
    raw_value: str,
    requirement: RequirementItem,
    metric: str,
) -> tuple[int, int] | None:
    exact_start = chunk_text.find(proposed_span)
    exact = (
        (exact_start, exact_start + len(proposed_span))
        if exact_start >= 0
        else _flexible_span(chunk_text, proposed_span)
    )
    if exact is not None:
        actual = chunk_text[exact[0] : exact[1]]
        if _raw_value_occurrences(actual, raw_value):
            return exact

    candidates: list[tuple[float, int, int]] = []
    for value_start, value_end in _raw_value_occurrences(chunk_text, raw_value):
        start, end = _source_window(chunk_text, value_start, value_end)
        context = chunk_text[start:end]
        if not _metric_supported(requirement, metric, context):
            continue
        score = max(
            lexical_score(term, context)
            for term in (requirement.name, *requirement.aliases)
        )
        candidates.append((score, start, end))
    if not candidates:
        return None
    _, start, end = max(candidates, key=lambda item: (item[0], -item[1]))
    return start, end


def _resolved_period(
    *,
    proposed_period: str | None,
    requirements: tuple[RequirementItem, ...],
    chunk: TextEvidenceReference,
    context: str,
) -> str | None:
    document_context = f"{chunk.document_name}\n{chunk.text}"
    if proposed_period:
        return (
            proposed_period
            if contains_phrase(context, proposed_period)
            or contains_phrase(document_context, proposed_period)
            else None
        )
    required_periods = tuple(
        item.name
        for item in requirements
        if item.required and item.kind == RequirementKind.PERIOD
    )
    if len(required_periods) != 1:
        return None
    period = required_periods[0]
    return (
        period
        if contains_phrase(context, period)
        or contains_phrase(document_context, period)
        else None
    )


def _metric_supported(
    requirement: RequirementItem,
    metric: str,
    context: str,
) -> bool:
    terms = (requirement.name, *requirement.aliases)
    if max(lexical_score(term, metric) for term in terms) < 0.76:
        return False
    return any(
        contains_phrase(context, term)
        or lexical_score(term, context) >= 0.76
        or (
            _metric_tokens(term)
            and _metric_tokens(term) <= _metric_tokens(context)
        )
        for term in terms
    )


def _metric_tokens(value: object) -> frozenset[str]:
    def singular(token: str) -> str:
        if token.endswith("ies") and len(token) > 4:
            return token[:-3] + "y"
        if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
            return token[:-1]
        return token

    return frozenset(
        singular(token)
        for token in _METRIC_TOKEN_RE.findall(str(value or "").casefold())
        if token not in _METRIC_TOKEN_STOP_WORDS
    )


def validate_text_extraction(
    *,
    response: TextExtractionResponse,
    requirements: tuple[RequirementItem, ...],
    chunks: tuple[TextEvidenceReference, ...],
    model: str,
    minimum_confidence: float = 0.75,
    stage: CompletionStage = CompletionStage.EXISTING_TEXT_EXTRACTION,
) -> TextExtractionValidationResult:
    requirements_by_id = {
        item.requirement_id: item for item in requirements
    }
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    facts: list[EvidenceFact] = []
    rejected: list[RejectedEvidence] = []

    def reject(proposed: ProposedEvidenceFact, reason: str) -> None:
        rejected.append(
            RejectedEvidence(
                stage=stage,
                reason=reason,
                requirement_id=proposed.requirement_id,
                document_id=proposed.document_id,
                chunk_id=proposed.chunk_id,
            )
        )

    for proposed in response.facts:
        requirement = requirements_by_id.get(proposed.requirement_id)
        chunk = chunks_by_id.get(proposed.chunk_id)
        if requirement is None or requirement.kind not in _SUBSTANTIVE_KINDS:
            reject(proposed, "Fact does not reference a substantive requirement.")
            continue
        if chunk is None or chunk.document_id != proposed.document_id:
            reject(proposed, "Fact references an unknown or mismatched source chunk.")
            continue
        if proposed.confidence < minimum_confidence:
            reject(proposed, "Extraction confidence is below the acceptance threshold.")
            continue
        grounded_span = _grounded_source_span(
            chunk_text=chunk.text,
            proposed_span=proposed.source_span,
            raw_value=proposed.raw_value,
            requirement=requirement,
            metric=proposed.metric,
        )
        if grounded_span is None:
            reject(proposed, "Supporting source span is not grounded in the chunk.")
            continue
        local_span_start, local_span_end = grounded_span
        source_span = chunk.text[local_span_start:local_span_end]
        span_start = chunk.text_offset + local_span_start
        span_end = chunk.text_offset + local_span_end
        if not _raw_value_occurrences(source_span, proposed.raw_value):
            reject(proposed, "Raw value is not present in the grounded source span.")
            continue
        normalized_value = _normalized_decimal(proposed.raw_value)
        if normalized_value is None:
            reject(proposed, "Raw value is not a supported explicit number.")
            continue
        context_start = max(0, local_span_start - 250)
        context_end = min(len(chunk.text), local_span_end + 250)
        context = chunk.text[context_start:context_end]
        if not _metric_supported(requirement, proposed.metric, context):
            reject(proposed, "Metric is not grounded near the supporting value.")
            continue
        period = _resolved_period(
            proposed_period=proposed.period,
            requirements=requirements,
            chunk=chunk,
            context=context,
        )
        if proposed.period and period is None:
            reject(proposed, "Period is not supported by the source document.")
            continue
        if (
            period
            and normalized_value == normalized_phrase(period)
            and not any(marker in proposed.raw_value for marker in "$€£₹.%")
        ):
            reject(proposed, "The extracted value is the period label, not a metric.")
            continue
        nearby_measurement_unit = _measurement_unit_near_value(
            source_span,
            proposed.raw_value,
        )
        proposed_unit = proposed.unit
        if (
            nearby_measurement_unit
            and (
                not proposed_unit
                or (
                    _MAGNITUDE_RE.search(proposed_unit)
                    and not _AREA_UNIT_RE.search(proposed_unit)
                )
            )
        ):
            proposed_unit = nearby_measurement_unit
        unit = _normalized_unit(proposed_unit, proposed.raw_value)
        if not _unit_supported(unit, context):
            reject(proposed, "Unit or scale is not supported by the source context.")
            continue
        if not _unit_semantically_compatible(requirement, unit):
            reject(
                proposed,
                "Measurement unit cannot quantify the requested metric.",
            )
            continue
        if proposed.entity and not (
            contains_phrase(chunk.text, proposed.entity)
            or contains_phrase(chunk.document_name, proposed.entity)
            or any(
                contains_phrase(chunk.text, entity)
                or contains_phrase(chunk.document_name, entity)
                for entity in requirement.entity_names
            )
        ):
            reject(proposed, "Entity is not grounded in the source document.")
            continue

        chunk_hash = chunk.content_hash or hashlib.sha256(
            chunk.text.encode("utf-8")
        ).hexdigest()
        identity = {
            "requirement_id": requirement.requirement_id,
            "document_id": chunk.document_id,
            "chunk_id": chunk.chunk_id,
            "chunk_hash": chunk_hash,
            "span_start": span_start,
            "raw_value": proposed.raw_value,
            "metric": normalized_phrase(proposed.metric),
            "period": normalized_phrase(period),
            "unit": normalized_phrase(unit),
        }
        facts.append(
            EvidenceFact(
                fact_id=stable_fact_id(identity),
                requirement_id=requirement.requirement_id,
                entity=proposed.entity,
                metric=proposed.metric,
                raw_value=proposed.raw_value,
                normalized_value=normalized_value,
                unit=unit,
                period=period,
                dimensions=proposed.dimensions,
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
                page=chunk.page_number,
                source_span=source_span,
                span_start=span_start,
                span_end=span_end,
                chunk_hash=chunk_hash,
                confidence=proposed.confidence,
                model=model,
            )
        )

    unique_facts = {
        fact.fact_id: fact for fact in sorted(
            facts,
            key=lambda item: (-item.confidence, item.fact_id),
        )
    }
    return TextExtractionValidationResult(
        facts=tuple(unique_facts.values()),
        rejected=tuple(rejected),
    )
