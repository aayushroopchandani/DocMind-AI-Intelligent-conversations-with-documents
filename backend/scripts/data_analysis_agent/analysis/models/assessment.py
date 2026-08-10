from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .completion import EvidenceFact
from .evidence import EvidencePackage
from .profile import DatasetProfiles
from .requirements import AnalysisRequirements
from .retrieval import RetrievalResult


EVIDENCE_ASSESSOR_VERSION = "1.13.0"
AMBIGUITY_PROMPT_VERSION = "1.0.0"


class CoverageStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    MISSING = "missing"
    CONFLICTING = "conflicting"
    AMBIGUOUS = "ambiguous"


class ReadinessDecision(str, Enum):
    READY = "ready"
    NEEDS_CANDIDATE_RESCUE = "needs_candidate_rescue"
    NEEDS_TEXT_EXTRACTION = "needs_text_extraction"
    NEEDS_RETRIEVAL_REPAIR = "needs_retrieval_repair"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNANSWERABLE = "unanswerable"


class EvidenceKind(str, Enum):
    DATASET = "dataset"
    DATASET_COLUMN = "dataset_column"
    TEXT_CHUNK = "text_chunk"
    FACT = "fact"


class MatchMethod(str, Enum):
    EXACT = "exact"
    VERIFIED_ALIAS = "verified_alias"
    PROFILE_PERIOD = "profile_period"
    DOCUMENT_SCOPE = "document_scope"
    UNIT = "unit"
    LEXICAL = "lexical"
    TABLE_SUMMARY = "table_summary"
    VALIDATED_EXTRACTION = "validated_extraction"
    LLM = "llm"


class EvidenceReference(BaseModel):
    evidence_kind: EvidenceKind
    document_id: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=240)
    confidence: float = Field(ge=0, le=1)
    match_method: MatchMethod
    dataset_id: str | None = None
    source_version: str | None = None
    table_id: str | None = None
    column_key: str | None = None
    chunk_id: str | None = None
    fact_id: str | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.evidence_kind in {
            EvidenceKind.DATASET,
            EvidenceKind.DATASET_COLUMN,
        } and not (self.dataset_id and self.source_version and self.table_id):
            raise ValueError("dataset evidence needs stable source identity")
        if self.evidence_kind == EvidenceKind.DATASET_COLUMN and not self.column_key:
            raise ValueError("column evidence needs a column key")
        if self.evidence_kind == EvidenceKind.TEXT_CHUNK and not self.chunk_id:
            raise ValueError("text evidence needs a chunk ID")
        if self.evidence_kind == EvidenceKind.FACT and not (
            self.fact_id and self.chunk_id
        ):
            raise ValueError("fact evidence needs fact and source chunk IDs")
        return self


class RequirementCoverage(BaseModel):
    requirement_id: str = Field(min_length=1)
    status: CoverageStatus
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=12)
    text_evidence_available: bool = False

    model_config = ConfigDict(frozen=True, extra="forbid")


class RequirementCombinationCoverage(BaseModel):
    """Evidence shared by requirements that must hold for one observation."""

    combination_id: str = Field(pattern=r"^coverage_[a-f0-9]{16}$")
    requirement_ids: tuple[str, ...] = Field(min_length=2, max_length=16)
    document_id: str | None = None
    status: CoverageStatus
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=12)

    model_config = ConfigDict(frozen=True, extra="forbid")


class DocumentCoverage(BaseModel):
    document_id: str = Field(min_length=1)
    document_name: str = ""
    required: bool
    status: CoverageStatus
    dataset_ids: tuple[str, ...] = ()
    text_chunk_ids: tuple[str, ...] = ()
    fact_ids: tuple[str, ...] = ()

    model_config = ConfigDict(frozen=True, extra="forbid")


class AssessmentDiagnostics(BaseModel):
    cache_hit: bool = False
    deterministic_match_count: int = Field(default=0, ge=0)
    ambiguity_candidate_count: int = Field(default=0, ge=0)
    ambiguity_resolved_count: int = Field(default=0, ge=0)
    ambiguity_llm_used: bool = False

    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceAssessment(BaseModel):
    """Deterministic readiness decision plus checkpoint-safe source references."""

    assessor_version: str = EVIDENCE_ASSESSOR_VERSION
    ambiguity_prompt_version: str = AMBIGUITY_PROMPT_VERSION
    ambiguity_model: str = Field(min_length=1)
    decision: ReadinessDecision
    coverage: tuple[RequirementCoverage, ...]
    combination_coverage: tuple[RequirementCombinationCoverage, ...] = Field(
        default=(),
        max_length=192,
    )
    document_coverage: tuple[DocumentCoverage, ...]
    required_count: int = Field(ge=0)
    supported_count: int = Field(ge=0)
    partial_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    conflicting_count: int = Field(ge=0)
    ambiguous_count: int = Field(ge=0)
    diagnostics: AssessmentDiagnostics = AssessmentDiagnostics()

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        requirement_ids = [item.requirement_id for item in self.coverage]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("coverage must contain unique requirement IDs")
        document_ids = [item.document_id for item in self.document_coverage]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("document coverage must contain unique document IDs")
        combination_ids = [
            item.combination_id for item in self.combination_coverage
        ]
        if len(combination_ids) != len(set(combination_ids)):
            raise ValueError("combination coverage IDs must be unique")
        if self.required_count > len(self.coverage):
            raise ValueError("required_count cannot exceed total coverage")
        counts = {
            CoverageStatus.SUPPORTED: self.supported_count,
            CoverageStatus.PARTIAL: self.partial_count,
            CoverageStatus.MISSING: self.missing_count,
            CoverageStatus.CONFLICTING: self.conflicting_count,
            CoverageStatus.AMBIGUOUS: self.ambiguous_count,
        }
        for status, expected in counts.items():
            if sum(item.status == status for item in self.coverage) != expected:
                raise ValueError(f"{status.value} count does not match coverage")
        return self


def assessment_cache_key(
    *,
    requirements: AnalysisRequirements,
    evidence: EvidencePackage,
    profiles: DatasetProfiles,
    retrieval: RetrievalResult,
    ambiguity_model: str,
    metadata_signatures: tuple[tuple[str, str], ...] = (),
    facts: tuple[EvidenceFact, ...] = (),
) -> str:
    requirement_payload = requirements.model_dump(
        mode="json",
        exclude={"diagnostics"},
    )
    payload = {
        "assessor_version": EVIDENCE_ASSESSOR_VERSION,
        "ambiguity_prompt_version": AMBIGUITY_PROMPT_VERSION,
        "ambiguity_model": ambiguity_model,
        "requirements": requirement_payload,
        "requirements_validation_conflicts": sorted(
            requirements.diagnostics.validation_conflicts
        ),
        "datasets": sorted(
            (item.dataset_id, item.source_version) for item in evidence.datasets
        ),
        "profile_version": profiles.profiler_version,
        "profile_failures": sorted(
            (item.dataset_id, item.reason.value) for item in profiles.failures
        ),
        "text_chunks": sorted(
            (item.document_id, item.chunk_id) for item in retrieval.text_evidence
        ),
        "facts": sorted(
            (
                item.fact_id,
                item.requirement_id,
                item.document_id,
                item.chunk_hash,
            )
            for item in facts
        ),
        "unresolved_tables": sorted(
            (item.document_id, item.table_id, item.reason)
            for item in evidence.unresolved_tables
        ),
        "metadata_signatures": sorted(metadata_signatures),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
