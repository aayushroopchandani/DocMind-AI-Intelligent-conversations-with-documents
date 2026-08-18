"""A bounded, redacted look at what the patch will do (Phase 9.12.2).

This is not the preview the user ultimately trusts — that one happens in the
browser, in a throwaway clone of the workbook, where the real engine renders the
real change. This is the summary the proposal card shows first: enough to judge
the patch without downloading a single payload chunk.

It is deliberately small and deliberately filtered. Small because a preview
lives in MongoDB and 9.9.4 keeps bulk data out of it. Filtered because the
Phase 8 privacy gateway decides what a sensitive value looks like, and inventing
a second policy here would eventually disagree with the first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from ..models.plans import PlanColumn
from ..models.privacy import AnalysisPrivacyMode
from ..privacy import PrivacyGateway
from .cells import CellState


if TYPE_CHECKING:  # A runtime import would close the placement/patches cycle.
    from ..placement.selection import PlacementDecision


class PatchPreview(BaseModel):
    """A bounded, redacted look at the change (9.12.2).

    Not authoritative and not applied to anything: the real preview happens in a
    throwaway workbook clone in the browser. This exists so the proposal card
    can show what is coming without downloading payload chunks.
    """

    target_range_a1: str = Field(min_length=5, max_length=100)
    total_rows: int = Field(ge=1)
    total_columns: int = Field(ge=1)
    header: tuple[str, ...] = Field(default=(), max_length=100)
    rows: tuple[tuple[str | None, ...], ...] = Field(default=(), max_length=50)
    replaced_rows: tuple[tuple[str | None, ...], ...] = Field(
        default=(),
        max_length=50,
    )
    sampled: bool = False
    redacted: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)


MAX_PREVIEW_ROWS = 20
MAX_PREVIEW_CELLS = 400
MAX_PREVIEW_TEXT = 120


def build_patch_preview(
    *,
    decision: PlacementDecision,
    columns: tuple[PlanColumn, ...],
    head: tuple[tuple[CellState, ...], ...],
    include_header: bool = True,
    privacy_mode: AnalysisPrivacyMode = AnalysisPrivacyMode.STANDARD,
    gateway: PrivacyGateway | None = None,
    max_rows: int = MAX_PREVIEW_ROWS,
) -> PatchPreview:
    """Return the preview for a compiled patch.

    `head` is the first rows of the result, captured while the payload was being
    written — no chunk is fetched back to build this.
    """

    rect = decision.target_rect
    width = rect.columns
    row_budget = max(1, min(max_rows, MAX_PREVIEW_CELLS // max(1, width)))
    redacted_keys = _redacted_columns(
        columns,
        head=head,
        include_header=include_header,
        privacy=gateway or PrivacyGateway(),
        privacy_mode=privacy_mode,
    )

    body = head[1:] if include_header and head else head
    sampled_rows = tuple(
        _present_row(row, redacted_keys, columns) for row in body[:row_budget]
    )
    replaced = ()
    if decision.overwrites and decision.before_cells is not None:
        replaced = tuple(
            _present_row(row, redacted_keys, columns)
            for row in decision.before_cells[:row_budget]
        )

    return PatchPreview(
        target_range_a1=decision.target_range_a1,
        total_rows=rect.rows,
        total_columns=width,
        header=(
            tuple(_text(cell) or "" for cell in head[0])
            if include_header and head
            else tuple(column.label for column in columns)
        ),
        rows=sampled_rows,
        replaced_rows=replaced,
        sampled=rect.rows - (1 if include_header else 0) > len(sampled_rows),
        redacted=bool(redacted_keys),
    )


def _redacted_columns(
    columns: tuple[PlanColumn, ...],
    *,
    head: tuple[tuple[CellState, ...], ...],
    include_header: bool,
    privacy: PrivacyGateway,
    privacy_mode: AnalysisPrivacyMode,
) -> frozenset[int]:
    """Return the column indexes whose sampled values must not be shown."""

    body = head[1:] if include_header and head else head
    redacted: set[int] = set()
    for index, column in enumerate(columns):
        values = tuple(
            str(row[index].value)
            for row in body
            if index < len(row) and isinstance(row[index].value, str)
        )
        decision = privacy.sanitize_examples(
            column_key=column.key,
            label=column.label,
            semantic_role="unknown",
            values=values,
            mode=privacy_mode,
        )
        if decision.redacted_count:
            redacted.add(index)
    return frozenset(redacted)


def _present_row(
    row: tuple[CellState, ...],
    redacted: frozenset[int],
    columns: tuple[PlanColumn, ...],
) -> tuple[str | None, ...]:
    return tuple(
        "[redacted]" if index in redacted else _text(cell)
        for index, cell in enumerate(row)
    )


def _text(cell: CellState) -> str | None:
    if cell.formula:
        return cell.formula[:MAX_PREVIEW_TEXT]
    if cell.value is None:
        return None
    if isinstance(cell.value, bool):
        return "TRUE" if cell.value else "FALSE"
    return str(cell.value)[:MAX_PREVIEW_TEXT]


__all__ = [
    "MAX_PREVIEW_CELLS",
    "PatchPreview",
    "MAX_PREVIEW_ROWS",
    "MAX_PREVIEW_TEXT",
    "build_patch_preview",
]
