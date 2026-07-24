from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Iterable

from ...models.requirements import (
    AnalysisOperation,
    AnalysisRequirements,
    ExpectedDataType,
    ExtractedRequirement,
    FilterOperator,
    RequirementItem,
    RequirementKind,
    RequirementOrigin,
    RequirementsDiagnostics,
    RequirementsExtraction,
    canonical_requirement_text,
    normalize_requirement_text,
    stable_requirement_id,
)
from ...models.request import AnalysisRequest


_EXPLICIT_PERIOD_RE = re.compile(
    r"\b(?:FY\s*)?(?:19|20)\d{2}\b"
    r"|\b(?:19|20)\d{2}\s*[-–/]\s*(?:\d{2}|(?:19|20)\d{2})\b"
    r"|\bQ[1-4]\s*(?:19|20)\d{2}\b"
    r"|\b(?:19|20)\d{2}\s*Q[1-4]\b",
    re.IGNORECASE,
)
_COMPARISON_RE = re.compile(
    r"\b(?:compare|comparison|versus|vs\.?|difference|across|both)\b",
    re.IGNORECASE,
)
_TREND_RE = re.compile(
    r"\b(?:trend|over\s+time|year[- ]over[- ]year|yoy|growth)\b",
    re.IGNORECASE,
)
_CORRELATION_RE = re.compile(
    r"\b(?:correlat(?:e|ion)|relationship|association)\b",
    re.IGNORECASE,
)
_ANOMALY_RE = re.compile(
    r"\b(?:anomal(?:y|ies)|outlier|unusual|abnormal)\b",
    re.IGNORECASE,
)
_ALL_DOCUMENTS_RE = re.compile(
    r"\b(?:all|both|each)\s+(?:documents?|reports?|companies?)\b"
    r"|\bacross\s+(?:the\s+)?(?:documents?|reports?|companies?)\b",
    re.IGNORECASE,
)
_TABLE_REQUIRED_RE = re.compile(
    r"\b(?:calculate|compute|exact|compare|trend|correlation|"
    r"rank|highest|lowest|year[- ]over[- ]year)\b",
    re.IGNORECASE,
)
_CURRENCY_RE = re.compile(r"\b(?:USD|INR|EUR|GBP)\b", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(?:%|\bpercent(?:age)?\b)", re.IGNORECASE)

_SAFE_ALIAS_GROUPS = (
    frozenset({"research and development", "r&d", "r and d"}),
    frozenset({"selling general and administrative", "sg&a", "s g and a"}),
    frozenset({"earnings per share", "eps"}),
    frozenset({"income from operations", "operating income"}),
    frozenset({"fiscal year", "fy"}),
    frozenset({"year over year", "yoy"}),
)


def _equivalence_form(value: str) -> str:
    normalized = canonical_requirement_text(value).replace("&", " and ")
    normalized = re.sub(
        r"\br\s+and\s+d\b",
        "research and development",
        normalized,
    )
    normalized = re.sub(
        r"\bsg\s+and\s+a\b",
        "selling general and administrative",
        normalized,
    )
    return " ".join(normalized.split())


def _strict_acronym_match(left: str, right: str) -> bool:
    words = re.findall(r"[a-z0-9]+", _equivalence_form(left))
    initials = "".join(word[0] for word in words if word not in {"and", "of", "the"})
    compact_right = re.sub(r"[^a-z0-9]", "", canonical_requirement_text(right))
    return len(initials) >= 2 and initials == compact_right


def _grounded_entity_name(query: str, entity: str) -> bool:
    if _mentioned(query, entity):
        return True
    return any(
        _strict_acronym_match(entity, token)
        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9&.-]{1,9}\b", query)
    )


@dataclass(frozen=True, slots=True)
class ValidationResult:
    requirements: AnalysisRequirements
    adjustments: tuple[str, ...]


def _filter_conflicts(
    items: Iterable[ExtractedRequirement],
) -> tuple[str, ...]:
    filters_by_name: dict[str, list[ExtractedRequirement]] = {}
    for item in items:
        if item.kind == RequirementKind.FILTER and item.required:
            filters_by_name.setdefault(
                canonical_requirement_text(item.name),
                [],
            ).append(item)
    conflicts: list[str] = []
    for name, filters in filters_by_name.items():
        equals_values = {
            canonical_requirement_text(value)
            for item in filters
            if item.filter_operator == FilterOperator.EQUALS
            for value in item.filter_values
        }
        excluded_values = {
            canonical_requirement_text(value)
            for item in filters
            if item.filter_operator == FilterOperator.NOT_EQUALS
            for value in item.filter_values
        }
        overlap = equals_values & excluded_values
        if overlap:
            conflicts.append(
                f"Filter '{name}' both includes and excludes: "
                + ", ".join(sorted(overlap))
            )
        if len(equals_values) > 1:
            conflicts.append(
                f"Filter '{name}' has multiple required equality values; "
                "clarify whether they should be combined."
            )
    return tuple(conflicts)


def _mentioned(query: str, value: str) -> bool:
    canonical_query = canonical_requirement_text(query)
    canonical_value = canonical_requirement_text(value)
    if not canonical_value:
        return False
    if re.fullmatch(r"[a-z0-9&]{2,8}", canonical_value):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(canonical_value)}(?![a-z0-9])",
                canonical_query,
            )
        )
    return canonical_value in canonical_query


def _safe_alias(canonical: str, alias: str, query: str) -> bool:
    canonical_name = canonical_requirement_text(canonical)
    alias_name = canonical_requirement_text(alias)
    if not alias_name or alias_name == canonical_name:
        return False
    equivalent_form = _equivalence_form(canonical) == _equivalence_form(alias)
    acronym_match = _strict_acronym_match(
        canonical,
        alias,
    ) or _strict_acronym_match(alias, canonical)
    verified_group = any(
        canonical_name in group and alias_name in group
        for group in _SAFE_ALIAS_GROUPS
    )
    if _mentioned(query, alias):
        return equivalent_form or acronym_match or verified_group
    return equivalent_form or verified_group


def _explicit_periods(query: str) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for match in _EXPLICIT_PERIOD_RE.finditer(query):
        value = normalize_requirement_text(match.group(0))
        canonical = re.sub(r"\s+", "", value).casefold()
        if canonical not in seen:
            seen.add(canonical)
            output.append(value)
    return tuple(output)


def _explicit_units(query: str) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for match in _CURRENCY_RE.finditer(query):
        unit = match.group(0).upper()
        if unit not in seen:
            seen.add(unit)
            output.append(unit)
    if _PERCENT_RE.search(query):
        output.append("percent")
    return tuple(output)


def _is_optional(query: str, name: str) -> bool:
    normalized_name = normalize_requirement_text(name)
    if not normalized_name:
        return False
    target = re.escape(normalized_name).replace(r"\ ", r"\s+")
    optional_before = re.compile(
        rf"\b(?:optional(?:ly)?|if\s+(?:possible|available))\b"
        rf"(?:\s+(?:include|add|show|provide))?\s+{target}\b",
        re.IGNORECASE,
    )
    optional_after = re.compile(
        rf"\b{target}\b(?:\s+only)?\s+"
        rf"(?:if\s+(?:possible|available)|where\s+available|optional)\b",
        re.IGNORECASE,
    )
    return bool(optional_before.search(query) or optional_after.search(query))


def _validated_aliases(
    item: ExtractedRequirement,
    query: str,
    adjustments: list[str],
) -> tuple[str, ...]:
    aliases: list[str] = []
    for alias in item.aliases:
        if _safe_alias(item.name, alias, query):
            aliases.append(alias)
        else:
            adjustments.append(
                f"dropped_unverified_alias:{canonical_requirement_text(item.name)}:"
                f"{canonical_requirement_text(alias)}"
            )
    return tuple(aliases)


def _is_grounded(item: ExtractedRequirement, query: str) -> bool:
    if item.kind in {
        RequirementKind.PERIOD,
        RequirementKind.UNIT,
        RequirementKind.FILTER,
    }:
        return _mentioned(query, item.name) or any(
            _mentioned(query, value) for value in item.filter_values
        )
    if item.kind in {
        RequirementKind.METRIC,
        RequirementKind.ENTITY,
        RequirementKind.DIMENSION,
    }:
        return _mentioned(query, item.name) or any(
            _mentioned(query, alias) and _safe_alias(item.name, alias, query)
            for alias in item.aliases
        )
    return True


def _resolved_operation(
    query: str,
    extracted: AnalysisOperation,
    adjustments: list[str],
) -> AnalysisOperation:
    explicit: AnalysisOperation | None = None
    if _CORRELATION_RE.search(query):
        explicit = AnalysisOperation.CORRELATION
    elif _ANOMALY_RE.search(query):
        explicit = AnalysisOperation.ANOMALY_DETECTION
    elif _COMPARISON_RE.search(query):
        explicit = AnalysisOperation.COMPARISON
    elif _TREND_RE.search(query):
        explicit = AnalysisOperation.TREND
    if explicit is not None and explicit != extracted:
        adjustments.append(
            f"operation_aligned_to_explicit_intent:{explicit.value}"
        )
        return explicit
    return extracted


def _dedupe_key(item: ExtractedRequirement) -> tuple[object, ...]:
    return (
        item.kind,
        canonical_requirement_text(item.name),
        tuple(sorted(canonical_requirement_text(v) for v in item.entity_names)),
        item.filter_operator,
        tuple(canonical_requirement_text(v) for v in item.filter_values),
    )


def _merge_extracted(
    items: Iterable[ExtractedRequirement],
    adjustments: list[str],
) -> tuple[ExtractedRequirement, ...]:
    merged: OrderedDict[tuple[object, ...], ExtractedRequirement] = OrderedDict()
    for item in items:
        key = _dedupe_key(item)
        existing = merged.get(key)
        if existing is None:
            merged[key] = item
            continue
        aliases = tuple(dict.fromkeys((*existing.aliases, *item.aliases)))[:4]
        merged[key] = existing.model_copy(
            update={
                "aliases": aliases,
                "required": existing.required or item.required,
                "expected_data_type": (
                    existing.expected_data_type
                    if existing.expected_data_type != ExpectedDataType.ANY
                    else item.expected_data_type
                ),
                "unit": existing.unit or item.unit,
            }
        )
        adjustments.append(
            f"merged_duplicate:{item.kind.value}:"
            f"{canonical_requirement_text(item.name)}"
        )
    return tuple(merged.values())


def _to_item(
    extracted: ExtractedRequirement,
    *,
    query: str,
    origin: RequirementOrigin,
    used_ids: set[str],
    adjustments: list[str],
) -> RequirementItem:
    base_id = stable_requirement_id(
        kind=extracted.kind,
        name=extracted.name,
        entity_names=extracted.entity_names,
        filter_operator=extracted.filter_operator,
        filter_values=extracted.filter_values,
    )
    requirement_id = base_id
    if requirement_id in used_ids:
        requirement_id = stable_requirement_id(
            kind=extracted.kind,
            name=extracted.name,
            entity_names=extracted.entity_names,
            filter_operator=extracted.filter_operator,
            filter_values=extracted.filter_values,
            disambiguate=True,
        )
    used_ids.add(requirement_id)
    explicitly_optional = _is_optional(query, extracted.name)
    explicitly_stated = _mentioned(query, extracted.name) or any(
        _mentioned(query, value) for value in extracted.filter_values
    )
    required = (
        False
        if explicitly_optional
        else True
        if explicitly_stated
        else extracted.required
    )
    if required != extracted.required:
        adjustment = (
            "preserved_optional"
            if explicitly_optional
            else "restored_explicit_required"
        )
        adjustments.append(f"{adjustment}:{requirement_id}")
    return RequirementItem(
        requirement_id=requirement_id,
        kind=extracted.kind,
        name=extracted.name,
        aliases=_validated_aliases(extracted, query, adjustments),
        required=required,
        expected_data_type=extracted.expected_data_type,
        unit=extracted.unit,
        entity_names=extracted.entity_names,
        filter_operator=extracted.filter_operator,
        filter_values=extracted.filter_values,
        origin=origin,
    )


def validate_requirements_extraction(
    *,
    request: AnalysisRequest,
    extraction: RequirementsExtraction,
    model: str,
    extraction_attempts: int,
    used_fallback: bool = False,
) -> ValidationResult:
    """Ground LLM output in explicit request constraints and normalize it."""

    query = request.query
    adjustments: list[str] = []
    grounded: list[ExtractedRequirement] = []
    for item in extraction.requirements:
        valid_entity_names = tuple(
            entity
            for entity in item.entity_names
            if _grounded_entity_name(query, entity)
        )
        if len(valid_entity_names) != len(item.entity_names):
            adjustments.append(
                f"dropped_ungrounded_entity_scope:"
                f"{canonical_requirement_text(item.name)}"
            )
            item = item.model_copy(update={"entity_names": valid_entity_names})
        if (
            item.kind == RequirementKind.METRIC
            and item.expected_data_type == ExpectedDataType.ANY
        ):
            item = item.model_copy(
                update={"expected_data_type": ExpectedDataType.NUMBER}
            )
            adjustments.append(
                f"enforced_numeric_metric:"
                f"{canonical_requirement_text(item.name)}"
            )
        elif (
            item.kind == RequirementKind.PERIOD
            and item.expected_data_type == ExpectedDataType.ANY
        ):
            item = item.model_copy(
                update={"expected_data_type": ExpectedDataType.DATE}
            )
        if _is_grounded(item, query):
            grounded.append(item)
        else:
            adjustments.append(
                f"dropped_ungrounded:{item.kind.value}:"
                f"{canonical_requirement_text(item.name)}"
            )

    existing_periods = {
        canonical_requirement_text(item.name)
        for item in grounded
        if item.kind == RequirementKind.PERIOD
    }
    for period in _explicit_periods(query):
        if canonical_requirement_text(period) not in existing_periods:
            grounded.append(
                ExtractedRequirement(
                    kind=RequirementKind.PERIOD,
                    name=period,
                    required=not _is_optional(query, period),
                    expected_data_type=ExpectedDataType.DATE,
                )
            )
            adjustments.append(
                f"restored_explicit_period:{canonical_requirement_text(period)}"
            )

    existing_units = {
        canonical_requirement_text(item.name)
        for item in grounded
        if item.kind == RequirementKind.UNIT
    }
    for unit in _explicit_units(query):
        if canonical_requirement_text(unit) not in existing_units:
            grounded.append(
                ExtractedRequirement(
                    kind=RequirementKind.UNIT,
                    name=unit,
                    required=not _is_optional(query, unit),
                )
            )
            adjustments.append(
                f"restored_explicit_unit:{canonical_requirement_text(unit)}"
            )

    grouping_names = {
        canonical_requirement_text(item.name)
        for item in grounded
        if item.kind == RequirementKind.DIMENSION
    }
    valid_groupings: list[str] = []
    for grouping in extraction.groupings:
        if not _mentioned(query, grouping):
            adjustments.append(
                f"dropped_ungrounded_grouping:{canonical_requirement_text(grouping)}"
            )
            continue
        valid_groupings.append(grouping)
        if canonical_requirement_text(grouping) not in grouping_names:
            grounded.append(
                ExtractedRequirement(
                    kind=RequirementKind.DIMENSION,
                    name=grouping,
                    required=True,
                )
            )
            grouping_names.add(canonical_requirement_text(grouping))

    if not grounded:
        grounded.append(
            ExtractedRequirement(
                kind=RequirementKind.TOPIC,
                name=query,
                required=True,
            )
        )
        used_fallback = True
        adjustments.append("added_conservative_topic_fallback")

    merged = _merge_extracted(grounded, adjustments)
    validation_conflicts = _filter_conflicts(merged)
    used_ids: set[str] = set()
    origin = (
        RequirementOrigin.FALLBACK if used_fallback else RequirementOrigin.LLM
    )
    items = tuple(
        _to_item(
            item,
            query=query,
            origin=(
                RequirementOrigin.EXPLICIT_GUARD
                if item.kind in {RequirementKind.PERIOD, RequirementKind.UNIT}
                and any(
                    adjustment.endswith(canonical_requirement_text(item.name))
                    for adjustment in adjustments
                    if adjustment.startswith("restored_explicit_")
                )
                else origin
            ),
            used_ids=used_ids,
            adjustments=adjustments,
        )
        for item in merged
    )
    operation = _resolved_operation(query, extraction.operation, adjustments)
    multi_document = len(request.document_ids) > 1
    requires_all_documents = multi_document and (
        extraction.requires_all_selected_documents
        or bool(_ALL_DOCUMENTS_RE.search(query))
        or (
            operation == AnalysisOperation.COMPARISON
            and sum(item.kind == RequirementKind.ENTITY for item in items) >= 2
        )
    )
    if requires_all_documents and not extraction.requires_all_selected_documents:
        adjustments.append("enforced_all_selected_document_coverage")

    has_metric = any(item.kind == RequirementKind.METRIC for item in items)
    table_required = extraction.table_evidence_required or bool(
        has_metric
        and (
            operation
            in {
                AnalysisOperation.COMPARISON,
                AnalysisOperation.TREND,
                AnalysisOperation.CORRELATION,
                AnalysisOperation.ANOMALY_DETECTION,
                AnalysisOperation.AGGREGATION,
                AnalysisOperation.RANKING,
                AnalysisOperation.DISTRIBUTION,
            }
            or _TABLE_REQUIRED_RE.search(query)
        )
    )
    if table_required and not extraction.table_evidence_required:
        adjustments.append("enforced_tabular_evidence")

    artifact = AnalysisRequirements(
        model=model,
        operation=operation,
        selected_document_ids=request.document_ids,
        requirements=items,
        groupings=tuple(valid_groupings),
        expected_granularity=extraction.expected_granularity,
        requires_join=extraction.requires_join,
        requires_all_selected_documents=requires_all_documents,
        table_evidence_required=table_required,
        text_evidence_acceptable=extraction.text_evidence_acceptable,
        diagnostics=RequirementsDiagnostics(
            cache_hit=False,
            extraction_attempts=extraction_attempts,
            used_fallback=used_fallback,
            validation_adjustments=tuple(dict.fromkeys(adjustments)),
            validation_conflicts=validation_conflicts,
        ),
    )
    return ValidationResult(
        requirements=artifact,
        adjustments=artifact.diagnostics.validation_adjustments,
    )


def fallback_extraction(request: AnalysisRequest) -> RequirementsExtraction:
    """Conservative deterministic artifact used only when the LLM is unavailable."""

    operation = AnalysisOperation.SUMMARIZATION
    if _CORRELATION_RE.search(request.query):
        operation = AnalysisOperation.CORRELATION
    elif _ANOMALY_RE.search(request.query):
        operation = AnalysisOperation.ANOMALY_DETECTION
    elif _COMPARISON_RE.search(request.query):
        operation = AnalysisOperation.COMPARISON
    elif _TREND_RE.search(request.query):
        operation = AnalysisOperation.TREND
    return RequirementsExtraction(
        operation=operation,
        requirements=(
            ExtractedRequirement(
                kind=RequirementKind.TOPIC,
                name=request.query,
                required=True,
            ),
        ),
        requires_all_selected_documents=(
            len(request.document_ids) > 1
            and bool(_ALL_DOCUMENTS_RE.search(request.query))
        ),
        table_evidence_required=bool(_TABLE_REQUIRED_RE.search(request.query)),
        text_evidence_acceptable=True,
    )
