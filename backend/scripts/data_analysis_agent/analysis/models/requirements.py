from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ANALYSIS_REQUIREMENTS_VERSION = "1.0.0"
REQUIREMENTS_PROMPT_VERSION = "1.0.0"

_SPACE_RE = re.compile(r"\s+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def normalize_requirement_text(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip(" .,:;")


def canonical_requirement_text(value: object) -> str:
    return normalize_requirement_text(value).casefold()


def _unique_text(values: object, *, limit: int = 12) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = normalize_requirement_text(value)
        canonical = text.casefold()
        if text and canonical not in seen:
            seen.add(canonical)
            output.append(text)
            if len(output) == limit:
                break
    return tuple(output)


class AnalysisOperation(str, Enum):
    COMPARISON = "comparison"
    TREND = "trend"
    AGGREGATION = "aggregation"
    CORRELATION = "correlation"
    ANOMALY_DETECTION = "anomaly_detection"
    RANKING = "ranking"
    DISTRIBUTION = "distribution"
    LOOKUP = "lookup"
    SUMMARIZATION = "summarization"
    OTHER = "other"


class RequirementKind(str, Enum):
    METRIC = "metric"
    ENTITY = "entity"
    PERIOD = "period"
    DIMENSION = "dimension"
    UNIT = "unit"
    FILTER = "filter"
    TOPIC = "topic"


class ExpectedDataType(str, Enum):
    NUMBER = "number"
    STRING = "string"
    BOOLEAN = "boolean"
    DATE = "date"
    ANY = "any"


class FilterOperator(str, Enum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    IN = "in"
    CONTAINS = "contains"
    BETWEEN = "between"


class RequirementOrigin(str, Enum):
    LLM = "llm"
    EXPLICIT_GUARD = "explicit_guard"
    FALLBACK = "fallback"


class RequirementItem(BaseModel):
    """One independently assessable requirement with a stable identity."""

    requirement_id: str = Field(
        pattern=r"^req_[a-z0-9_]{1,96}$",
        min_length=5,
        max_length=100,
    )
    kind: RequirementKind
    name: str = Field(min_length=1, max_length=160)
    aliases: tuple[str, ...] = Field(default=(), max_length=4)
    required: bool = True
    expected_data_type: ExpectedDataType = ExpectedDataType.ANY
    unit: str | None = Field(default=None, max_length=80)
    entity_names: tuple[str, ...] = Field(default=(), max_length=8)
    filter_operator: FilterOperator | None = None
    filter_values: tuple[str, ...] = Field(default=(), max_length=12)
    origin: RequirementOrigin = RequirementOrigin.LLM

    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value: object) -> str:
        return normalize_requirement_text(value)

    @field_validator("aliases", "entity_names", "filter_values", mode="before")
    @classmethod
    def clean_text_tuple(cls, value: object) -> tuple[str, ...]:
        return _unique_text(value)

    @field_validator("unit", mode="before")
    @classmethod
    def clean_unit(cls, value: object) -> str | None:
        text = normalize_requirement_text(value)
        return text or None

    @model_validator(mode="after")
    def validate_filter_fields(self) -> Self:
        is_filter = self.kind == RequirementKind.FILTER
        if is_filter and (self.filter_operator is None or not self.filter_values):
            raise ValueError("filter requirements need an operator and values")
        if not is_filter and (
            self.filter_operator is not None or self.filter_values
        ):
            raise ValueError("filter details are only valid for filter requirements")
        return self


class RequirementsDiagnostics(BaseModel):
    cache_hit: bool = False
    extraction_attempts: int = Field(default=0, ge=0, le=2)
    used_fallback: bool = False
    validation_adjustments: tuple[str, ...] = Field(default=(), max_length=40)
    validation_conflicts: tuple[str, ...] = Field(default=(), max_length=20)

    model_config = ConfigDict(frozen=True, extra="forbid")


class AnalysisRequirements(BaseModel):
    """Validated analytical intent; contains no source data or execution plan."""

    requirements_version: str = ANALYSIS_REQUIREMENTS_VERSION
    prompt_version: str = REQUIREMENTS_PROMPT_VERSION
    model: str = Field(min_length=1)
    operation: AnalysisOperation
    selected_document_ids: tuple[str, ...] = Field(min_length=1)
    requirements: tuple[RequirementItem, ...] = Field(min_length=1, max_length=48)
    groupings: tuple[str, ...] = Field(default=(), max_length=12)
    expected_granularity: str | None = Field(default=None, max_length=160)
    requires_join: bool = False
    requires_all_selected_documents: bool = False
    table_evidence_required: bool = False
    text_evidence_acceptable: bool = True
    diagnostics: RequirementsDiagnostics = RequirementsDiagnostics()

    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("selected_document_ids", "groupings", mode="before")
    @classmethod
    def clean_tuple(cls, value: object) -> tuple[str, ...]:
        return _unique_text(value, limit=64)

    @field_validator("expected_granularity", mode="before")
    @classmethod
    def clean_granularity(cls, value: object) -> str | None:
        text = normalize_requirement_text(value)
        return text or None

    @model_validator(mode="after")
    def validate_requirements(self) -> Self:
        identifiers = [item.requirement_id for item in self.requirements]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("requirement IDs must be unique")
        if self.requires_all_selected_documents and len(self.selected_document_ids) < 2:
            raise ValueError(
                "all-document coverage only applies to multi-document requests"
            )
        return self


class ExtractedRequirement(BaseModel):
    """Structured LLM output before deterministic validation and stable IDs."""

    kind: RequirementKind
    name: str = Field(min_length=1, max_length=160)
    aliases: tuple[str, ...] = Field(default=(), max_length=4)
    required: bool = True
    expected_data_type: ExpectedDataType = ExpectedDataType.ANY
    unit: str | None = Field(default=None, max_length=80)
    entity_names: tuple[str, ...] = Field(default=(), max_length=8)
    filter_operator: FilterOperator | None = None
    filter_values: tuple[str, ...] = Field(default=(), max_length=12)

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value: object) -> str:
        return normalize_requirement_text(value)

    @field_validator("aliases", "entity_names", "filter_values", mode="before")
    @classmethod
    def clean_tuple(cls, value: object) -> tuple[str, ...]:
        return _unique_text(value)

    @field_validator("unit", mode="before")
    @classmethod
    def clean_optional_unit(cls, value: object) -> str | None:
        text = normalize_requirement_text(value)
        return text or None

    @model_validator(mode="after")
    def validate_filter_fields(self) -> Self:
        if self.kind == RequirementKind.FILTER:
            if self.filter_operator is None or not self.filter_values:
                raise ValueError("filter requirements need an operator and values")
        else:
            self.filter_operator = None
            self.filter_values = ()
        return self


class RequirementsExtraction(BaseModel):
    """The complete schema returned by the requirements LLM."""

    operation: AnalysisOperation
    requirements: tuple[ExtractedRequirement, ...] = Field(
        min_length=1,
        max_length=48,
    )
    groupings: tuple[str, ...] = Field(default=(), max_length=12)
    expected_granularity: str | None = Field(default=None, max_length=160)
    requires_join: bool = False
    requires_all_selected_documents: bool = False
    table_evidence_required: bool = False
    text_evidence_acceptable: bool = True

    model_config = ConfigDict(extra="forbid")

    @field_validator("groupings", mode="before")
    @classmethod
    def clean_groupings(cls, value: object) -> tuple[str, ...]:
        return _unique_text(value)

    @field_validator("expected_granularity", mode="before")
    @classmethod
    def clean_granularity(cls, value: object) -> str | None:
        text = normalize_requirement_text(value)
        return text or None


def stable_requirement_id(
    *,
    kind: RequirementKind,
    name: str,
    entity_names: tuple[str, ...] = (),
    filter_operator: FilterOperator | None = None,
    filter_values: tuple[str, ...] = (),
    disambiguate: bool = False,
) -> str:
    slug = _SLUG_RE.sub("_", canonical_requirement_text(name)).strip("_")[:48]
    if not slug:
        slug = "value"
    base = f"req_{kind.value}_{slug}"
    if not disambiguate:
        return base
    identity = json.dumps(
        {
            "kind": kind.value,
            "name": canonical_requirement_text(name),
            "entities": sorted(canonical_requirement_text(v) for v in entity_names),
            "operator": filter_operator.value if filter_operator else None,
            "values": [canonical_requirement_text(v) for v in filter_values],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    return f"{base[:87]}_{digest}"


def requirements_cache_key(
    *,
    query: str,
    document_ids: tuple[str, ...],
    prompt_version: str,
    model: str,
) -> str:
    payload: dict[str, Any] = {
        "query": canonical_requirement_text(query),
        "document_ids": sorted(document_ids),
        "prompt_version": prompt_version,
        "model": model,
        "requirements_version": ANALYSIS_REQUIREMENTS_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
