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
    r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
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
}
_SUBSTANTIVE_KINDS = frozenset(
    {
        RequirementKind.METRIC,
        RequirementKind.DIMENSION,
        RequirementKind.FILTER,
        RequirementKind.TOPIC,
    }
)


@dataclass(frozen=True, slots=True)
class TextExtractionValidationResult:
    facts: tuple[EvidenceFact, ...]
    rejected: tuple[RejectedEvidence, ...]


def _normalized_decimal(raw_value: str) -> str | None:
    match = _NUMBER_RE.search(raw_value)
    if match is None:
        return None
    try:
        value = Decimal(match.group("number").replace(",", ""))
    except InvalidOperation:
        return None
    if match.group("negative") or raw_value.strip().startswith("-"):
        value = -value
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


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
        for term in terms
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
        local_span_start = chunk.text.find(proposed.source_span)
        if local_span_start < 0:
            reject(proposed, "Supporting source span is not present verbatim.")
            continue
        local_span_end = local_span_start + len(proposed.source_span)
        span_start = chunk.text_offset + local_span_start
        span_end = chunk.text_offset + local_span_end
        if proposed.raw_value not in proposed.source_span:
            reject(proposed, "Raw value is not present in the supporting span.")
            continue
        normalized_value = _normalized_decimal(proposed.raw_value)
        if normalized_value is None:
            reject(proposed, "Raw value is not a supported explicit number.")
            continue
        if (
            proposed.period
            and normalized_value == normalized_phrase(proposed.period)
            and not any(
                marker in proposed.raw_value for marker in "$€£₹.%"
            )
        ):
            reject(proposed, "The extracted value is the period label, not a metric.")
            continue
        context_start = max(0, local_span_start - 250)
        context_end = min(len(chunk.text), local_span_end + 250)
        context = chunk.text[context_start:context_end]
        if not _metric_supported(requirement, proposed.metric, context):
            reject(proposed, "Metric is not grounded near the supporting value.")
            continue
        if proposed.period and not contains_phrase(context, proposed.period):
            reject(proposed, "Period is not supported near the source value.")
            continue
        if not _unit_supported(proposed.unit, context):
            reject(proposed, "Unit or scale is not supported by the source context.")
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
            "period": normalized_phrase(proposed.period),
            "unit": normalized_phrase(proposed.unit),
        }
        facts.append(
            EvidenceFact(
                fact_id=stable_fact_id(identity),
                requirement_id=requirement.requirement_id,
                entity=proposed.entity,
                metric=proposed.metric,
                raw_value=proposed.raw_value,
                normalized_value=normalized_value,
                unit=proposed.unit,
                period=proposed.period,
                dimensions=proposed.dimensions,
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
                page=chunk.page_number,
                source_span=proposed.source_span,
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
