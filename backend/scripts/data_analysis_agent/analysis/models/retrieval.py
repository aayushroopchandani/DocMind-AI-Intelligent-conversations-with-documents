from __future__ import annotations

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
    relevance_score: float | None = Field(default=None, ge=0)
    matched_queries: tuple[str, ...] = ()
    retrieval_modes: tuple[str, ...] = ()

    model_config = ConfigDict(frozen=True, extra="forbid")


class RetrievedTableReference(BaseModel):
    table_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    title: str = ""
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    expected_columns: tuple[str, ...] = ()
    expected_units: tuple[str, ...] = ()
    relevance_score: float | None = Field(default=None, ge=0)
    rrf_score: float | None = Field(default=None, ge=0)
    matched_queries: tuple[str, ...] = ()
    retrieval_modes: tuple[str, ...] = ()

    model_config = ConfigDict(frozen=True, extra="forbid")


class RetrievalDiagnostics(BaseModel):
    query_generation_attempts: int = Field(default=0, ge=0)
    query_generation_fallback: bool = False

    model_config = ConfigDict(frozen=True, extra="forbid")


class RetrievalResult(BaseModel):
    """Lean parent-facing result adapted from the retrieval child state."""

    retrieval_scope: Literal["normal", "broad"]
    table_intent: Literal["required", "supporting", "none"]
    signals: RetrievalSignals
    text_evidence: tuple[TextEvidenceReference, ...] = ()
    table_references: tuple[RetrievedTableReference, ...] = ()
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
                    title=str(candidate.get("title") or "").strip(),
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
            diagnostics=RetrievalDiagnostics(
                query_generation_attempts=max(
                    0, int(state.get("query_generation_attempts") or 0)
                ),
                query_generation_fallback=bool(
                    state.get("query_generation_fallback", False)
                ),
            ),
        )
