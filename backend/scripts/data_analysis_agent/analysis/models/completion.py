from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .evidence import HydratedDatasetReference
from .profile import DatasetProfiles
from .retrieval import TableCandidateReference, TextEvidenceReference


EVIDENCE_COMPLETION_VERSION = "1.5.0"
TEXT_EVIDENCE_EXTRACTOR_VERSION = "1.5.0"
TEXT_EVIDENCE_PROMPT_VERSION = "1.1.0"
REPAIR_RETRIEVAL_CACHE_VERSION = "1.1.0"


def completion_utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CompletionStage(str, Enum):
    CANDIDATE_RESCUE = "candidate_rescue"
    EXISTING_TEXT_EXTRACTION = "existing_text_extraction"
    TARGETED_RETRIEVAL = "targeted_retrieval"
    REPAIR_TEXT_EXTRACTION = "repair_text_extraction"


class CompletionStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    EXHAUSTED = "exhausted"
    SKIPPED = "skipped"


class DatasetAdditionOrigin(str, Enum):
    CANDIDATE_RESCUE = "candidate_rescue"
    RETRIEVAL_REPAIR = "retrieval_repair"


class CompletionAttemptOutcome(str, Enum):
    CACHE_HIT = "cache_hit"
    EVIDENCE_ADDED = "evidence_added"
    NO_MATCH = "no_match"
    FAILED = "failed"


class FactDimension(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=200)

    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceFact(BaseModel):
    """Bounded, validated numeric fact extracted from source text."""

    fact_id: str = Field(pattern=r"^fact_[a-f0-9]{24}$")
    requirement_id: str = Field(min_length=1, max_length=100)
    entity: str = Field(min_length=1, max_length=200)
    metric: str = Field(min_length=1, max_length=200)
    raw_value: str = Field(min_length=1, max_length=120)
    normalized_value: str = Field(min_length=1, max_length=120)
    unit: str | None = Field(default=None, max_length=100)
    period: str | None = Field(default=None, max_length=100)
    dimensions: tuple[FactDimension, ...] = Field(default=(), max_length=12)
    document_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    source_span: str = Field(min_length=1, max_length=600)
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)
    chunk_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confidence: float = Field(ge=0, le=1)
    extractor_version: str = TEXT_EVIDENCE_EXTRACTOR_VERSION
    prompt_version: str = TEXT_EVIDENCE_PROMPT_VERSION
    model: str = Field(min_length=1)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        if self.span_end <= self.span_start:
            raise ValueError("source span end must be after its start")
        return self


class DerivedDatasetColumn(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    type: Literal["string", "number"]

    model_config = ConfigDict(frozen=True, extra="forbid")


class DerivedDatasetReference(BaseModel):
    """Checkpoint-safe reference; derived rows remain in MongoDB."""

    derived_dataset_id: str = Field(pattern=r"^derived_[a-f0-9]{24}$")
    document_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=1000)
    source_chunk_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    source_content_hashes: tuple[str, ...] = Field(min_length=1, max_length=20)
    requirement_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    columns: tuple[DerivedDatasetColumn, ...] = Field(min_length=1, max_length=12)
    row_count: int = Field(ge=1)
    unit: str | None = Field(default=None, max_length=100)
    periods: tuple[str, ...] = Field(default=(), max_length=50)
    validation_status: Literal["validated"] = "validated"
    reusability_status: Literal["cached", "promotion_candidate"] = "cached"
    access_provider: Literal["mongodb"] = "mongodb"
    access_collection: Literal["derived_datasets"] = "derived_datasets"
    extractor_version: str = TEXT_EVIDENCE_EXTRACTOR_VERSION
    prompt_version: str = TEXT_EVIDENCE_PROMPT_VERSION
    model: str = Field(min_length=1)

    model_config = ConfigDict(frozen=True, extra="forbid")


class AugmentedDatasetReference(BaseModel):
    origin: DatasetAdditionOrigin
    requirement_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    dataset: HydratedDatasetReference

    model_config = ConfigDict(frozen=True, extra="forbid")


class RejectedEvidence(BaseModel):
    stage: CompletionStage
    reason: str = Field(min_length=1, max_length=300)
    requirement_id: str | None = Field(default=None, max_length=100)
    document_id: str | None = None
    chunk_id: str | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")


class CompletionAttempt(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=80)
    stage: CompletionStage
    outcome: CompletionAttemptOutcome
    requirement_ids: tuple[str, ...] = Field(default=(), max_length=30)
    document_ids: tuple[str, ...] = Field(default=(), max_length=10)
    queries: tuple[str, ...] = Field(default=(), max_length=12)
    discovered_table_count: int = Field(default=0, ge=0)
    hydrated_table_count: int = Field(default=0, ge=0)
    accepted_fact_count: int = Field(default=0, ge=0)
    cache_hit: bool = False
    reason: str = Field(default="", max_length=300)

    model_config = ConfigDict(frozen=True, extra="forbid")


class AugmentedEvidence(BaseModel):
    """Phase 6 additions and lineage; never mutates the base evidence package."""

    completion_version: str = EVIDENCE_COMPLETION_VERSION
    run_id: str = Field(min_length=1)
    base_evidence_signature: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: CompletionStatus
    base_dataset_ids: tuple[str, ...] = Field(default=(), max_length=30)
    base_text_chunk_ids: tuple[str, ...] = Field(default=(), max_length=30)
    added_datasets: tuple[AugmentedDatasetReference, ...] = Field(
        default=(),
        max_length=16,
    )
    additional_profiles: DatasetProfiles | None = None
    facts: tuple[EvidenceFact, ...] = Field(default=(), max_length=30)
    derived_datasets: tuple[DerivedDatasetReference, ...] = Field(
        default=(),
        max_length=10,
    )
    attempts: tuple[CompletionAttempt, ...] = Field(default=(), max_length=16)
    rejected_evidence: tuple[RejectedEvidence, ...] = Field(
        default=(),
        max_length=30,
    )
    remaining_requirement_ids: tuple[str, ...] = Field(default=(), max_length=48)
    final_decision: Literal[
        "ready",
        "needs_candidate_rescue",
        "needs_text_extraction",
        "needs_retrieval_repair",
        "needs_clarification",
        "unanswerable",
    ]
    created_at: datetime = Field(default_factory=completion_utc_now)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_unique_evidence(self) -> Self:
        if (
            self.status == CompletionStatus.READY
            and self.final_decision != "ready"
        ):
            raise ValueError("ready completion must have a ready decision")
        if (
            self.status == CompletionStatus.EXHAUSTED
            and self.final_decision != "unanswerable"
        ):
            raise ValueError(
                "exhausted completion must have an unanswerable decision"
            )
        if len(self.base_dataset_ids) != len(set(self.base_dataset_ids)):
            raise ValueError("base dataset references must be unique")
        if len(self.base_text_chunk_ids) != len(set(self.base_text_chunk_ids)):
            raise ValueError("base text references must be unique")
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("augmented evidence facts must be unique")
        dataset_ids = [
            addition.dataset.dataset_id for addition in self.added_datasets
        ]
        if len(dataset_ids) != len(set(dataset_ids)):
            raise ValueError("augmented datasets must be unique")
        derived_ids = [
            item.derived_dataset_id for item in self.derived_datasets
        ]
        if len(derived_ids) != len(set(derived_ids)):
            raise ValueError("derived dataset references must be unique")
        attempt_ids = [item.attempt_id for item in self.attempts]
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("completion attempt IDs must be unique")
        if self.additional_profiles is not None:
            profile_ids = {
                item.dataset_id
                for item in (
                    *self.additional_profiles.profiles,
                    *self.additional_profiles.failures,
                )
            }
            if not profile_ids.issubset(set(dataset_ids)):
                raise ValueError(
                    "additional profiles must refer to augmented datasets"
                )
        return self


def base_evidence_signature(
    *,
    dataset_versions: tuple[tuple[str, str], ...],
    text_chunks: tuple[tuple[str, str], ...],
) -> str:
    payload: dict[str, Any] = {
        "datasets": sorted(dataset_versions),
        "text_chunks": sorted(text_chunks),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ProposedEvidenceFact(BaseModel):
    """Small schema returned by the extraction LLM before validation."""

    requirement_id: str = Field(min_length=1, max_length=100)
    entity: str = Field(min_length=1, max_length=200)
    metric: str = Field(min_length=1, max_length=200)
    raw_value: str = Field(min_length=1, max_length=120)
    unit: str | None = Field(default=None, max_length=100)
    period: str | None = Field(default=None, max_length=100)
    dimensions: tuple[FactDimension, ...] = Field(default=(), max_length=12)
    document_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    source_span: str = Field(min_length=1, max_length=600)
    confidence: float = Field(ge=0, le=1)

    model_config = ConfigDict(extra="forbid")

    @field_validator("source_span", mode="before")
    @classmethod
    def trim_source_span(cls, value: object) -> str:
        return str(value or "").strip()[:600]


class TextExtractionResponse(BaseModel):
    status: Literal["absent", "evidence"]
    facts: tuple[ProposedEvidenceFact, ...] = Field(default=(), max_length=30)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "absent" and self.facts:
            raise ValueError("absent extraction cannot contain facts")
        if self.status == "evidence" and not self.facts:
            raise ValueError("evidence extraction must contain facts")
        return self


class TextExtractionCacheEntry(BaseModel):
    status: Literal["absent", "accepted", "rejected"]
    facts: tuple[EvidenceFact, ...] = Field(default=(), max_length=30)
    derived_datasets: tuple[DerivedDatasetReference, ...] = Field(
        default=(),
        max_length=10,
    )
    rejected_evidence: tuple[RejectedEvidence, ...] = Field(
        default=(),
        max_length=30,
    )
    expires_at: datetime

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_cached_status(self) -> Self:
        if self.status == "accepted" and not self.facts:
            raise ValueError("accepted cache entries require validated facts")
        if self.status == "absent" and (
            self.facts or self.derived_datasets or self.rejected_evidence
        ):
            raise ValueError("absent cache entries cannot contain evidence")
        return self


class RepairRetrievalCacheEntry(BaseModel):
    """Short-lived targeted-retrieval result, including negative results."""

    queries: tuple[str, ...] = Field(min_length=1, max_length=12)
    document_ids: tuple[str, ...] = Field(min_length=1, max_length=10)
    table_candidates: tuple[TableCandidateReference, ...] = Field(
        default=(),
        max_length=30,
    )
    text_evidence: tuple[TextEvidenceReference, ...] = Field(
        default=(),
        max_length=30,
    )
    expires_at: datetime

    model_config = ConfigDict(frozen=True, extra="forbid")


def stable_fact_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "fact_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
