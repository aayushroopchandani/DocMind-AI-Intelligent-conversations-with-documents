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
from .explicit import (
    ExplicitCategoryConstraint,
    explicit_category_constraints,
    semantically_grounded_in_query,
)


_EXPLICIT_PERIOD_RE = re.compile(
    r"\b(?:FY\s*)?(?:19|20)\d{2}\b"
    r"|\b(?:19|20)\d{2}\s*[-–/]\s*(?:\d{2}|(?:19|20)\d{2})\b"
    r"|\bQ[1-4]\s*(?:19|20)\d{2}\b"
    r"|\b(?:19|20)\d{2}\s*Q[1-4]\b",
    re.IGNORECASE,
)
_YEAR_SPAN_RE = re.compile(
    r"\b(?P<start>(?:19|20)\d{2})\s+(?:through|to)\s+"
    r"(?P<end>(?:19|20)\d{2})\b",
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
_VALID_UNIT_RE = re.compile(
    r"(?:₹|\$|€|£|\b(?:USD|INR|EUR|GBP|dollars?|rupees?|euros?|pounds?|"
    r"thousands?|millions?|billions?|crores?|lakhs?|percent(?:age)?)\b)",
    re.IGNORECASE,
)
_METRIC_LIST_RE = re.compile(
    r"\b(?:compare|show|analy[sz]e|calculate|compute|report|plot|track)\s+"
    r"(?P<items>.+?)"
    r"(?=\s+(?:for|in|from|during|over|across|between)\s+"
    r"(?:FY\s*)?(?:19|20)\d{2}\b|[?.!]|$)",
    re.IGNORECASE,
)
_METRIC_WORD_RE = re.compile(
    r"\b(?:program\s+services|general\s+and\s+administration|fundraising|"
    r"research\s+and\s+development(?:\s+(?:expense|expenditure)s?)?|"
    r"(?:total|net|gross|operating|current|noncurrent|non-current)?\s*"
    r"(?:assets?|liabilit(?:y|ies)|revenues?|sales|expenses?|costs?|"
    r"income|earnings|profits?|loss(?:es)?|margin|cash(?:\s+flows?)?|"
    r"equity|debt|headcount|employees?|rate|ratio|returns?|"
    r"expenditures?|contributions?|grants?|support))\b",
    re.IGNORECASE,
)

_SAFE_ALIAS_GROUPS = (
    frozenset({"research and development", "r&d", "r and d"}),
    frozenset({"selling general and administrative", "sg&a", "s g and a"}),
    frozenset({"earnings per share", "eps"}),
    frozenset({"income from operations", "operating income"}),
    frozenset({"fiscal year", "fy"}),
    frozenset({"year over year", "yoy"}),
)
_GENERIC_PERIOD_NAMES = frozenset(
    {"year", "years", "period", "periods", "fiscal year", "fiscal years"}
)
_GENERIC_TOPIC_TOKENS = frozenset(
    {"schedule", "table", "data", "details", "information", "overview", "summary"}
)
_ENTITY_SCOPE_SPLIT_RE = re.compile(
    r"\b(?:with|versus|vs\.?|compared\s+(?:with|to))\b",
    re.IGNORECASE,
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
    if _mentioned(query, entity) or semantically_grounded_in_query(query, entity):
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
    for match in _YEAR_SPAN_RE.finditer(query):
        start = int(match.group("start"))
        end = int(match.group("end"))
        if end < start or end - start > 50:
            continue
        for year in range(start, end + 1):
            value = str(year)
            if value not in seen:
                seen.add(value)
                output.append(value)
    return tuple(output)


def _requirement_tokens(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", canonical_requirement_text(value)))


def _drop_redundant_generic_requirements(
    items: list[ExtractedRequirement],
    *,
    explicit_periods: tuple[str, ...],
    adjustments: list[str],
) -> list[ExtractedRequirement]:
    metrics = tuple(
        _requirement_tokens(item.name)
        for item in items
        if item.kind == RequirementKind.METRIC
    )
    output: list[ExtractedRequirement] = []
    explicit_period_names = {
        canonical_requirement_text(period) for period in explicit_periods
    }
    for item in items:
        canonical_name = canonical_requirement_text(item.name)
        if (
            explicit_periods
            and item.kind == RequirementKind.PERIOD
            and canonical_name in _GENERIC_PERIOD_NAMES
        ):
            adjustments.append(f"dropped_redundant_period:{canonical_name}")
            continue
        period_span = (
            _YEAR_SPAN_RE.fullmatch(canonical_name)
            if item.kind == RequirementKind.PERIOD
            else None
        )
        if period_span is not None:
            start = int(period_span.group("start"))
            end = int(period_span.group("end"))
            expanded = {str(year) for year in range(start, end + 1)}
            if expanded and expanded <= explicit_period_names:
                adjustments.append(
                    f"dropped_redundant_period_range:{canonical_name}"
                )
                continue
        if item.kind == RequirementKind.TOPIC and metrics:
            topic_tokens = _requirement_tokens(item.name) - _GENERIC_TOPIC_TOKENS
            if topic_tokens and any(
                topic_tokens <= metric_tokens for metric_tokens in metrics
            ):
                adjustments.append(f"dropped_redundant_topic:{canonical_name}")
                continue
        output.append(item)
    return output


def _remove_category_entity_scopes(
    items: list[ExtractedRequirement],
    *,
    constraints: tuple[ExplicitCategoryConstraint, ...],
    adjustments: list[str],
) -> list[ExtractedRequirement]:
    category_entities = {
        canonical_requirement_text(value)
        for constraint in constraints
        for value in (
            *constraint.values,
            *(
                f"{constraint.name} {category_value}"
                for category_value in constraint.values
            ),
        )
    }
    if not category_entities:
        return items
    output: list[ExtractedRequirement] = []
    for item in items:
        if not item.entity_names:
            output.append(item)
            continue
        retained = tuple(
            entity
            for entity in item.entity_names
            if canonical_requirement_text(entity) not in category_entities
        )
        if retained != item.entity_names:
            adjustments.append(
                "removed_category_entity_scope:"
                f"{canonical_requirement_text(item.name)}"
            )
            item = item.model_copy(update={"entity_names": retained})
        output.append(item)
    return output


def _entity_candidates(
    items: list[ExtractedRequirement],
) -> tuple[str, ...]:
    metrics = tuple(
        item.name for item in items if item.kind == RequirementKind.METRIC
    )
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        values = (
            (item.name,)
            if item.kind == RequirementKind.ENTITY
            else item.entity_names
        )
        for entity in values:
            normalized_entity = normalize_requirement_text(entity)
            candidate = normalized_entity
            for metric in sorted(metrics, key=len, reverse=True):
                normalized_metric = normalize_requirement_text(metric)
                entity_tokens = normalized_entity.split()
                metric_tokens = normalized_metric.split()
                if (
                    len(entity_tokens) > len(metric_tokens)
                    and entity_tokens[-len(metric_tokens) :] == metric_tokens
                ):
                    candidate = " ".join(
                        entity.split()[: -len(metric_tokens)]
                    ).strip()
                    break
            canonical_candidate = canonical_requirement_text(candidate)
            if candidate and canonical_candidate not in seen:
                seen.add(canonical_candidate)
                output.append(candidate)
    return tuple(output)


def _repair_metric_entity_scopes(
    items: list[ExtractedRequirement],
    *,
    query: str,
    adjustments: list[str],
) -> list[ExtractedRequirement]:
    """Correct cross-document metric scopes using explicit query segments."""

    candidates = _entity_candidates(items)
    segments = tuple(
        segment.strip(" ,.;:")
        for segment in _ENTITY_SCOPE_SPLIT_RE.split(query)
        if segment.strip(" ,.;:")
    )
    if len(candidates) < 2 or len(segments) < 2:
        return items
    output: list[ExtractedRequirement] = []
    for item in items:
        if item.kind != RequirementKind.METRIC:
            output.append(item)
            continue
        terms = (item.name, *item.aliases)
        metric_segments = tuple(
            segment
            for segment in segments
            if any(_mentioned(segment, term) for term in terms)
        )
        scoped = tuple(
            candidate
            for candidate in candidates
            if any(
                _mentioned(segment, candidate)
                for segment in metric_segments
            )
        )
        if scoped and scoped != item.entity_names:
            adjustments.append(
                "repaired_metric_entity_scope:"
                f"{canonical_requirement_text(item.name)}"
            )
            item = item.model_copy(update={"entity_names": scoped})
        output.append(item)
    return output


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


def _explicit_metric_list(query: str) -> tuple[str, ...]:
    """Recover only clear, coordinated metric lists from the original request."""

    match = _METRIC_LIST_RE.search(query)
    if match is None:
        return ()
    phrase = normalize_requirement_text(match.group("items"))
    if "," not in phrase:
        return ()
    parts = re.split(r"\s*,\s*", phrase)
    output: list[str] = []
    for part in parts:
        candidate = normalize_requirement_text(part)
        candidate = re.sub(
            r"^(?:(?:and|the|their|its)\s+)+",
            "",
            candidate,
            flags=re.IGNORECASE,
        )
        metric_match = _METRIC_WORD_RE.search(candidate)
        if candidate and len(candidate.split()) <= 8 and metric_match:
            output.append(normalize_requirement_text(metric_match.group(0)))
    return tuple(dict.fromkeys(output)) if len(output) >= 2 else ()


def _metric_is_present(
    candidate: str,
    requirements: Iterable[ExtractedRequirement],
) -> bool:
    canonical_candidate = canonical_requirement_text(candidate)
    return any(
        item.kind == RequirementKind.METRIC
        and (
            canonical_candidate == canonical_requirement_text(item.name)
            or canonical_candidate in canonical_requirement_text(item.name)
            or any(
                canonical_candidate == canonical_requirement_text(alias)
                or canonical_candidate in canonical_requirement_text(alias)
                for alias in item.aliases
            )
        )
        for item in requirements
    )


def _has_valid_unit(value: str) -> bool:
    return bool(_VALID_UNIT_RE.search(value))


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
        return (
            _mentioned(query, item.name)
            or semantically_grounded_in_query(query, item.name)
            or any(
            _mentioned(query, alias) and _safe_alias(item.name, alias, query)
            for alias in item.aliases
        )
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
        if item.kind == RequirementKind.UNIT and not _has_valid_unit(item.name):
            adjustments.append(
                f"dropped_invalid_unit:{canonical_requirement_text(item.name)}"
            )
            continue
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

    explicit_periods = _explicit_periods(query)
    existing_periods = {
        canonical_requirement_text(item.name)
        for item in grounded
        if item.kind == RequirementKind.PERIOD
    }
    for period in explicit_periods:
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

    explicit_metric_list = _explicit_metric_list(query)
    for metric in explicit_metric_list:
        if not _metric_is_present(metric, grounded):
            grounded.append(
                ExtractedRequirement(
                    kind=RequirementKind.METRIC,
                    name=metric,
                    required=not _is_optional(query, metric),
                    expected_data_type=ExpectedDataType.NUMBER,
                )
            )
            adjustments.append(
                f"restored_explicit_metric:{canonical_requirement_text(metric)}"
            )

    if explicit_metric_list:
        explicit_names = {
            canonical_requirement_text(metric)
            for metric in explicit_metric_list
        }
        precise_names = tuple(explicit_names)
        filtered_grounded: list[ExtractedRequirement] = []
        for item in grounded:
            item_name = canonical_requirement_text(item.name)
            collapsed_generic = (
                item.kind == RequirementKind.METRIC
                and item_name not in explicit_names
                and any(
                    item_name
                    and item_name in precise_name
                    and item_name != precise_name
                    for precise_name in precise_names
                )
            )
            if collapsed_generic:
                adjustments.append(f"dropped_collapsed_metric:{item_name}")
            else:
                filtered_grounded.append(item)
        grounded = filtered_grounded
    seen_metrics: set[str] = set()
    for match in _METRIC_WORD_RE.finditer(query):
        metric = normalize_requirement_text(match.group(0))
        canonical = canonical_requirement_text(metric)
        if (
            not canonical
            or canonical in seen_metrics
            or _metric_is_present(metric, grounded)
        ):
            continue
        seen_metrics.add(canonical)
        grounded.append(
            ExtractedRequirement(
                kind=RequirementKind.METRIC,
                name=metric,
                required=not _is_optional(query, metric),
                expected_data_type=ExpectedDataType.NUMBER,
            )
        )
        adjustments.append(f"restored_explicit_metric:{canonical}")

    grounded = _drop_redundant_generic_requirements(
        grounded,
        explicit_periods=explicit_periods,
        adjustments=adjustments,
    )

    existing_filters = {
        (
            canonical_requirement_text(item.name),
            tuple(canonical_requirement_text(value) for value in item.filter_values),
        )
        for item in grounded
        if item.kind == RequirementKind.FILTER
    }
    explicit_category_names: list[str] = []
    category_constraints = explicit_category_constraints(query)
    for constraint in category_constraints:
        key = (
            canonical_requirement_text(constraint.name),
            tuple(
                canonical_requirement_text(value)
                for value in constraint.values
            ),
        )
        explicit_category_names.append(constraint.name)
        if key in existing_filters:
            continue
        grounded.append(
            ExtractedRequirement(
                kind=RequirementKind.FILTER,
                name=constraint.name,
                required=True,
                expected_data_type=ExpectedDataType.STRING,
                filter_operator=FilterOperator.IN,
                filter_values=constraint.values,
            )
        )
        adjustments.append(
            "restored_explicit_filter:"
            f"{canonical_requirement_text(constraint.name)}:"
            + ",".join(
                canonical_requirement_text(value)
                for value in constraint.values
            )
        )

    grounded = _remove_category_entity_scopes(
        grounded,
        constraints=category_constraints,
        adjustments=adjustments,
    )
    grounded = _repair_metric_entity_scopes(
        grounded,
        query=query,
        adjustments=adjustments,
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
    for grouping in explicit_category_names:
        canonical_grouping = canonical_requirement_text(grouping)
        if canonical_grouping not in {
            canonical_requirement_text(value) for value in valid_groupings
        }:
            valid_groupings.append(grouping)

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
                if item.kind
                in {
                    RequirementKind.METRIC,
                    RequirementKind.PERIOD,
                    RequirementKind.UNIT,
                    RequirementKind.FILTER,
                }
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
    scoped_entity_names = {
        canonical_requirement_text(entity)
        for item in items
        for entity in item.entity_names
        if canonical_requirement_text(entity)
    }
    requires_all_documents = multi_document and (
        extraction.requires_all_selected_documents
        or bool(_ALL_DOCUMENTS_RE.search(query))
        or (
            operation == AnalysisOperation.COMPARISON
            and len(scoped_entity_names) >= 2
        )
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
