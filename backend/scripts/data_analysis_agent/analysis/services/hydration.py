from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from db.models.structured_table import StructuredTable

from ..models import (
    AnalysisIssue,
    DatasetAccessReference,
    DatasetColumn,
    EvidencePackage,
    HydratedDatasetReference,
    IssueCode,
    IssueSeverity,
    IssueStage,
    RetrievedTableReference,
    SourceRegion,
    UnresolvedTableReference,
)
from ..repositories import HydrationSourceBatch
from .versioning import raw_dataset_id, source_version


@dataclass(frozen=True, slots=True)
class HydrationOutcome:
    package: EvidencePackage
    warnings: tuple[AnalysisIssue, ...] = ()


def _max_score(left: float | None, right: float | None) -> float | None:
    values = [value for value in (left, right) if value is not None]
    return max(values) if values else None


def deduplicate_table_references(
    references: tuple[RetrievedTableReference, ...],
) -> tuple[RetrievedTableReference, ...]:
    """Merge duplicate discoveries in O(reference count) while preserving order."""

    output: list[RetrievedTableReference] = []
    positions: dict[str, int] = {}
    for reference in references:
        position = positions.get(reference.table_id)
        if position is None:
            positions[reference.table_id] = len(output)
            output.append(reference)
            continue

        existing = output[position]
        output[position] = existing.model_copy(
            update={
                "matched_queries": tuple(
                    dict.fromkeys((*existing.matched_queries, *reference.matched_queries))
                ),
                "retrieval_modes": tuple(
                    dict.fromkeys((*existing.retrieval_modes, *reference.retrieval_modes))
                ),
                "relevance_score": _max_score(
                    existing.relevance_score,
                    reference.relevance_score,
                ),
                "rrf_score": _max_score(existing.rrf_score, reference.rrf_score),
            }
        )
    return tuple(output)


def _stale_fields(
    reference: RetrievedTableReference,
    table: StructuredTable,
) -> tuple[str, ...]:
    fields: list[str] = []
    if reference.document_id != table.document_id:
        fields.append("document_id")
    if reference.title and reference.title != table.title:
        fields.append("title")
    if reference.page_start and reference.page_start != table.page_start:
        fields.append("page_start")
    if reference.page_end and reference.page_end != table.page_end:
        fields.append("page_end")
    if reference.expected_columns and reference.expected_columns != tuple(
        column.key for column in table.columns
    ):
        fields.append("columns")
    actual_units = {
        column.unit for column in table.columns if column.unit is not None
    }
    if reference.expected_units and set(reference.expected_units) != actual_units:
        fields.append("units")
    return tuple(fields)


def _status(retrieved: int, hydrated: int) -> Literal["complete", "partial", "empty"]:
    if retrieved == 0 or hydrated == 0:
        return "empty"
    return "complete" if retrieved == hydrated else "partial"


def _document_index(
    documents: tuple[dict[str, Any], ...],
) -> dict[str, Mapping[str, Any]]:
    return {
        document_id: document
        for document in documents
        if (document_id := str(document.get("document_id") or "").strip())
    }


def _dataset_reference(
    *,
    table: StructuredTable,
    reference: RetrievedTableReference,
    document_name: str,
    usable: bool,
) -> HydratedDatasetReference:
    version = source_version(table)
    return HydratedDatasetReference(
        dataset_id=raw_dataset_id(table, version),
        source_version=version,
        table_id=table.table_id,
        document_id=table.document_id,
        document_name=document_name,
        title=table.title,
        page_start=table.page_start,
        page_end=table.page_end,
        extraction_method=table.extraction_method,
        columns=tuple(
            DatasetColumn(
                key=column.key,
                label=column.label,
                type=column.type,
                unit=column.unit,
            )
            for column in table.columns
        ),
        row_count=len(table.rows),
        source_regions=tuple(
            SourceRegion(
                page=fragment.page,
                bounding_box=tuple(fragment.bounding_box),
            )
            for fragment in table.source_fragments
        ),
        access=DatasetAccessReference(table_id=table.table_id),
        usable_for_analysis=usable,
        retrieval_score=(
            reference.relevance_score
            if reference.relevance_score is not None
            else reference.rrf_score
        ),
        matched_queries=reference.matched_queries,
        retrieval_modes=reference.retrieval_modes,
    )


class EvidenceHydrator:
    """Convert authoritative source records into immutable evidence handles."""

    def hydrate(
        self,
        *,
        run_id: str,
        user_id: str,
        document_ids: Sequence[str],
        references: tuple[RetrievedTableReference, ...],
        sources: HydrationSourceBatch,
    ) -> HydrationOutcome:
        allowed_document_ids = set(document_ids)
        raw_tables = {
            table_id: table
            for table in sources.tables
            if (table_id := str(table.get("table_id") or "").strip())
        }
        documents = _document_index(sources.documents)
        datasets: list[HydratedDatasetReference] = []
        unresolved: list[UnresolvedTableReference] = []
        warnings: list[AnalysisIssue] = []

        for reference in references:
            raw_table = raw_tables.get(reference.table_id)
            if raw_table is None:
                unresolved.append(
                    UnresolvedTableReference(
                        table_id=reference.table_id,
                        document_id=reference.document_id,
                        reason="not_available",
                    )
                )
                warnings.append(
                    AnalysisIssue(
                        code=IssueCode.TABLE_NOT_AVAILABLE,
                        severity=IssueSeverity.WARNING,
                        stage=IssueStage.HYDRATION,
                        message=(
                            "A retrieved table is missing, stale, or outside the "
                            "authorized document scope."
                        ),
                        table_id=reference.table_id,
                        document_id=reference.document_id,
                    )
                )
                continue

            try:
                table = StructuredTable.model_validate(raw_table)
            except ValidationError:
                unresolved.append(
                    UnresolvedTableReference(
                        table_id=reference.table_id,
                        document_id=reference.document_id,
                        reason="invalid",
                    )
                )
                warnings.append(
                    AnalysisIssue(
                        code=IssueCode.INVALID_TABLE,
                        severity=IssueSeverity.WARNING,
                        stage=IssueStage.HYDRATION,
                        message="A source table failed schema validation.",
                        table_id=reference.table_id,
                        document_id=reference.document_id,
                    )
                )
                continue

            if (
                table.user_id != user_id
                or table.document_id not in allowed_document_ids
            ):
                unresolved.append(
                    UnresolvedTableReference(
                        table_id=reference.table_id,
                        document_id=reference.document_id,
                        reason="not_available",
                    )
                )
                warnings.append(
                    AnalysisIssue(
                        code=IssueCode.TABLE_NOT_AVAILABLE,
                        severity=IssueSeverity.WARNING,
                        stage=IssueStage.HYDRATION,
                        message=(
                            "A retrieved table is missing, stale, or outside the "
                            "authorized document scope."
                        ),
                        table_id=reference.table_id,
                        document_id=reference.document_id,
                    )
                )
                continue

            stale_fields = _stale_fields(reference, table)
            if stale_fields:
                warnings.append(
                    AnalysisIssue(
                        code=IssueCode.STALE_RETRIEVAL_METADATA,
                        severity=IssueSeverity.WARNING,
                        stage=IssueStage.HYDRATION,
                        message=(
                            "MongoDB replaced stale retrieval metadata for: "
                            + ", ".join(stale_fields)
                            + "."
                        ),
                        table_id=table.table_id,
                        document_id=table.document_id,
                    )
                )

            document = documents.get(table.document_id)
            document_name = ""
            if document is None:
                warnings.append(
                    AnalysisIssue(
                        code=IssueCode.DOCUMENT_METADATA_NOT_FOUND,
                        severity=IssueSeverity.WARNING,
                        stage=IssueStage.HYDRATION,
                        message="Document display metadata is unavailable.",
                        table_id=table.table_id,
                        document_id=table.document_id,
                    )
                )
            else:
                document_name = str(document.get("filename") or "").strip()
                if (
                    document.get("ingestion_status") not in {None, "ready"}
                    or document.get("table_ingestion_status") not in {None, "ready"}
                ):
                    warnings.append(
                        AnalysisIssue(
                            code=IssueCode.DOCUMENT_NOT_READY,
                            severity=IssueSeverity.WARNING,
                            stage=IssueStage.HYDRATION,
                            message="The source document is not fully ingestion-ready.",
                            table_id=table.table_id,
                            document_id=table.document_id,
                            retryable=True,
                        )
                    )

            usable = bool(table.rows)
            if not usable:
                warnings.append(
                    AnalysisIssue(
                        code=IssueCode.EMPTY_TABLE,
                        severity=IssueSeverity.WARNING,
                        stage=IssueStage.HYDRATION,
                        message="The source table contains no rows.",
                        table_id=table.table_id,
                        document_id=table.document_id,
                    )
                )
            datasets.append(
                _dataset_reference(
                    table=table,
                    reference=reference,
                    document_name=document_name,
                    usable=usable,
                )
            )

        return HydrationOutcome(
            package=EvidencePackage(
                run_id=run_id,
                status=_status(len(references), len(datasets)),
                datasets=tuple(datasets),
                unresolved_tables=tuple(unresolved),
                retrieved_table_count=len(references),
                hydrated_table_count=len(datasets),
            ),
            warnings=tuple(warnings),
        )
