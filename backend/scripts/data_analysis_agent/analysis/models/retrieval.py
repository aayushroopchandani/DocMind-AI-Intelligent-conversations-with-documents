from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _unique_text(values: object) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split()).strip(" .,:;")
        canonical = text.casefold()
        if text and canonical not in seen:
            seen.add(canonical)
            output.append(text)
    return tuple(output)


def _optional_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


def _optional_score(value: object) -> float | None:
    try:
        return max(0.0, float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class RetrievalConcept(BaseModel):
    canonical: str = Field(min_length=1)
    variants: tuple[str, ...] = ()
    kind: Literal["metric", "entity", "dimension", "topic"]

    model_config = ConfigDict(frozen=True, extra="forbid")


class RetrievalSignals(BaseModel):
    concepts: tuple[RetrievalConcept, ...] = ()
    metrics: tuple[str, ...] = ()
    years: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    column_terms: tuple[str, ...] = ()

    model_config = ConfigDict(frozen=True, extra="forbid")


class TextEvidenceReference(BaseModel):
    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    document_name: str = ""
    page_number: int | None = Field(default=None, ge=1)
    text: str
    content_hash: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    text_offset: int = Field(default=0, ge=0)
    relevance_score: float | None = Field(default=None, ge=0)
    matched_queries: tuple[str, ...] = ()
    retrieval_modes: tuple[str, ...] = ()

    model_config = ConfigDict(frozen=True, extra="forbid")


class RetrievedTableReference(BaseModel):
    table_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    source_type: Literal[
        "pdf_table",
        "spreadsheet_range",
        "uploaded_csv",
        "uploaded_xlsx",
        "derived_dataset",
        "generated_dataset",
    ] = "pdf_table"
    source_version: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    title: str = Field(default="", max_length=240)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    expected_columns: tuple[str, ...] = ()
    expected_units: tuple[str, ...] = ()
    relevance_score: float | None = Field(default=None, ge=0)
    rrf_score: float | None = Field(default=None, ge=0)
    matched_queries: tuple[str, ...] = ()
    retrieval_modes: tuple[str, ...] = ()

    model_config = ConfigDict(frozen=True, extra="forbid")


class TableCandidateReference(BaseModel):
    """Compact pre-fusion table candidate retained for bounded rescue."""

    table_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    title: str = Field(default="", max_length=240)
    summary: str = Field(default="", max_length=1600)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    expected_columns: tuple[str, ...] = Field(default=(), max_length=64)
    expected_metrics: tuple[str, ...] = Field(default=(), max_length=32)
    expected_units: tuple[str, ...] = Field(default=(), max_length=16)
    keywords: tuple[str, ...] = Field(default=(), max_length=32)
    rrf_score: float | None = Field(default=None, ge=0)
    matched_queries: tuple[str, ...] = Field(default=(), max_length=12)
    retrieval_modes: tuple[str, ...] = Field(default=(), max_length=4)

    model_config = ConfigDict(frozen=True, extra="forbid")

    def as_retrieved_reference(self) -> RetrievedTableReference:
        return RetrievedTableReference(
            table_id=self.table_id,
            document_id=self.document_id,
            title=self.title,
            page_start=self.page_start,
            page_end=self.page_end,
            expected_columns=self.expected_columns,
            expected_units=self.expected_units,
            rrf_score=self.rrf_score,
            matched_queries=self.matched_queries,
            retrieval_modes=self.retrieval_modes,
        )


class RetrievalDiagnostics(BaseModel):
    query_generation_attempts: int = Field(default=0, ge=0)
    query_generation_fallback: bool = False
    query_generation_cache_hit: bool = False

    model_config = ConfigDict(frozen=True, extra="forbid")


class RetrievalResult(BaseModel):
    """Lean parent-facing result adapted from the retrieval child state."""

    retrieval_scope: Literal["normal", "broad"]
    table_intent: Literal["required", "supporting", "none"]
    signals: RetrievalSignals
    text_evidence: tuple[TextEvidenceReference, ...] = ()
    table_references: tuple[RetrievedTableReference, ...] = ()
    table_candidates: tuple[TableCandidateReference, ...] = Field(
        default=(),
        max_length=30,
    )
    diagnostics: RetrievalDiagnostics = RetrievalDiagnostics()

    model_config = ConfigDict(frozen=True, extra="forbid")

    @classmethod
    def from_retrieval_state(cls, state: Mapping[str, Any]) -> "RetrievalResult":
        text_evidence: list[TextEvidenceReference] = []
        for candidate in state.get("final_text_chunks", []):
            if not isinstance(candidate, Mapping):
                continue
            metadata = candidate.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            text_evidence.append(
                TextEvidenceReference(
                    chunk_id=str(candidate.get("chunk_id") or "").strip(),
                    document_id=str(
                        metadata.get("doc_id")
                        or metadata.get("document_id")
                        or ""
                    ).strip(),
                    document_name=str(metadata.get("source") or "").strip(),
                    page_number=_optional_positive_int(
                        metadata.get("page_number") or metadata.get("page")
                    ),
                    text=str(candidate.get("text") or ""),
                    content_hash=hashlib.sha256(
                        str(candidate.get("text") or "").encode("utf-8")
                    ).hexdigest(),
                    relevance_score=_optional_score(
                        candidate.get("relevance_score")
                    ),
                    matched_queries=_unique_text(candidate.get("matched_queries")),
                    retrieval_modes=_unique_text(candidate.get("retrieval_modes")),
                )
            )

        table_references: list[RetrievedTableReference] = []
        for candidate in state.get("final_tables", []):
            if not isinstance(candidate, Mapping):
                continue
            table_references.append(
                RetrievedTableReference(
                    table_id=str(candidate.get("table_id") or "").strip(),
                    document_id=str(candidate.get("document_id") or "").strip(),
                    source_type=str(
                        candidate.get("source_type") or "pdf_table"
                    ),
                    source_version=(
                        str(candidate.get("source_version") or "").strip()
                        or None
                    ),
                    title=str(candidate.get("title") or "").strip()[:240],
                    page_start=_optional_positive_int(candidate.get("page_start")),
                    page_end=_optional_positive_int(candidate.get("page_end")),
                    expected_columns=_unique_text(candidate.get("columns")),
                    expected_units=_unique_text(candidate.get("units")),
                    relevance_score=_optional_score(
                        candidate.get("relevance_score")
                    ),
                    rrf_score=_optional_score(candidate.get("rrf_score")),
                    matched_queries=_unique_text(candidate.get("matched_queries")),
                    retrieval_modes=_unique_text(candidate.get("retrieval_modes")),
                )
            )

        table_candidates: list[TableCandidateReference] = []
        seen_candidate_ids: set[str] = set()
        for candidate in state.get("retrieved_tables", []):
            if not isinstance(candidate, Mapping):
                continue
            table_id = str(candidate.get("table_id") or "").strip()
            document_id = str(candidate.get("document_id") or "").strip()
            if not table_id or not document_id or table_id in seen_candidate_ids:
                continue
            seen_candidate_ids.add(table_id)
            table_candidates.append(
                TableCandidateReference(
                    table_id=table_id,
                    document_id=document_id,
                    title=str(candidate.get("title") or "").strip()[:240],
                    summary=str(candidate.get("summary") or "")[:1600],
                    page_start=_optional_positive_int(candidate.get("page_start")),
                    page_end=_optional_positive_int(candidate.get("page_end")),
                    expected_columns=_unique_text(candidate.get("columns"))[:64],
                    expected_metrics=_unique_text(candidate.get("metrics"))[:32],
                    expected_units=_unique_text(candidate.get("units"))[:16],
                    keywords=_unique_text(candidate.get("keywords"))[:32],
                    rrf_score=_optional_score(candidate.get("rrf_score")),
                    matched_queries=_unique_text(
                        candidate.get("matched_queries")
                    )[:12],
                    retrieval_modes=_unique_text(
                        candidate.get("retrieval_modes")
                    )[:4],
                )
            )

        concepts = tuple(
            RetrievalConcept(
                canonical=str(value.get("canonical") or "").strip(),
                variants=_unique_text(value.get("variants")),
                kind=str(value.get("kind") or "topic"),
            )
            for value in state.get("match_concepts", [])
            if isinstance(value, Mapping) and str(value.get("canonical") or "").strip()
        )
        return cls(
            retrieval_scope=str(state.get("retrieval_scope") or "normal"),
            table_intent=str(state.get("table_intent") or "supporting"),
            signals=RetrievalSignals(
                concepts=concepts,
                metrics=_unique_text(state.get("metrics")),
                years=_unique_text(state.get("years")),
                entities=_unique_text(state.get("entities")),
                units=_unique_text(state.get("units")),
                column_terms=_unique_text(state.get("column_terms")),
            ),
            text_evidence=tuple(text_evidence),
            table_references=tuple(table_references),
            table_candidates=tuple(table_candidates[:30]),
            diagnostics=RetrievalDiagnostics(
                query_generation_attempts=max(
                    0, int(state.get("query_generation_attempts") or 0)
                ),
                query_generation_fallback=bool(
                    state.get("query_generation_fallback", False)
                ),
                query_generation_cache_hit=bool(
                    state.get("query_generation_cache_hit", False)
                ),
            ),
        )
