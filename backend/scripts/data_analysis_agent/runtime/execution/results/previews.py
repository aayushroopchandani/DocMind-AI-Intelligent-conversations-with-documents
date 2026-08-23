"""Bounded, redacted preview rows (Phase 9.9.1).

A preview is the only place real cell values leave the execution boundary, so it
is deliberately small and deliberately filtered. Two rules apply:

* **bounded** — a fixed row and cell cap, because previews travel through
  MongoDB and SSE, and 9.9.4 forbids full tables in either;
* **redacted** — the Phase 8 privacy gateway decides what a sensitive value
  looks like, and this reuses it rather than inventing a second policy.

Text is also neutralized against formula injection, because a preview can be
rendered into a spreadsheet-like grid and a value beginning with `=` should
never become live there.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ....runtime.models.plans import PlanColumn
from ....runtime.models.privacy import AnalysisPrivacyMode
from ....runtime.privacy import PrivacyGateway
from ...formulas.safety import neutralize_text


MAX_PREVIEW_ROWS = 20
MAX_PREVIEW_CELLS = 400
MAX_PREVIEW_TEXT = 120

MAX_PREVIEW_BYTES = 1024 * 1024
"""Download cap for a stored preview member.

The caps above bound a preview to roughly 400 short cells, so a megabyte is
generous by an order of magnitude. It exists so a read path can never be talked
into pulling an unbounded object by a reference that claims to be a preview.
"""


class ResultPreview(BaseModel):
    """The shape of a stored `result.preview.json`, for readers.

    It lives beside :func:`build_preview` so the writer and the reader cannot
    drift apart unnoticed; a test asserts one parses the other.

    The caps are re-applied on read. They were already enforced when the
    document was written, but a read path that trusts stored bytes to still be
    bounded is a read path that can be handed an unbounded one.

    `extra="ignore"` rather than the codebase's usual `forbid`: a preview
    published by a newer format version should degrade to the fields this
    reader understands, not fail the whole request for a result that is
    otherwise perfectly readable.
    """

    row_count: int = Field(ge=0)
    preview_row_count: int = Field(ge=0, le=MAX_PREVIEW_ROWS)
    truncated: bool = False
    privacy_mode: str = Field(max_length=40)
    redacted_column_keys: tuple[str, ...] = Field(default=(), max_length=500)
    columns: tuple[str, ...] = Field(default=(), max_length=500)
    rows: tuple[dict[str, JsonValue], ...] = Field(
        default=(),
        max_length=MAX_PREVIEW_ROWS,
    )

    model_config = ConfigDict(extra="ignore", frozen=True)


def build_preview(
    frame: pl.DataFrame,
    columns: tuple[PlanColumn, ...],
    *,
    privacy_mode: AnalysisPrivacyMode = AnalysisPrivacyMode.STANDARD,
    gateway: PrivacyGateway | None = None,
    max_rows: int = MAX_PREVIEW_ROWS,
) -> dict[str, Any]:
    """Return a bounded, redacted preview of `frame`."""

    privacy = gateway or PrivacyGateway()
    row_budget = max(0, min(max_rows, MAX_PREVIEW_ROWS))
    if columns:
        row_budget = min(row_budget, MAX_PREVIEW_CELLS // len(columns) or 1)
    head = frame.head(row_budget)

    redacted_keys: list[str] = []
    sanitized: dict[str, list[Any]] = {}
    for column in columns:
        values = head.get_column(column.key).to_list() if head.height else []
        decision = privacy.sanitize_examples(
            column_key=column.key,
            label=column.label,
            semantic_role="unknown",
            values=tuple(
                str(value) for value in values if isinstance(value, str)
            ),
            mode=privacy_mode,
        )
        if decision.redacted_count:
            redacted_keys.append(column.key)
            sanitized[column.key] = ["[redacted]"] * len(values)
        else:
            sanitized[column.key] = [_present(value) for value in values]

    return {
        "row_count": frame.height,
        "preview_row_count": head.height,
        "truncated": frame.height > head.height,
        "privacy_mode": privacy_mode.value,
        "redacted_column_keys": redacted_keys,
        "columns": [column.key for column in columns],
        "rows": [
            {column.key: sanitized[column.key][index] for column in columns}
            for index in range(head.height)
        ],
    }


def _present(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        # A preview may be rendered in a grid; a leading `=` must not go live.
        return neutralize_text(value[:MAX_PREVIEW_TEXT])
    if isinstance(value, (int, float, bool)):
        return value
    return str(value)[:MAX_PREVIEW_TEXT]


__all__ = [
    "ResultPreview",
    "MAX_PREVIEW_BYTES",
    "MAX_PREVIEW_CELLS",
    "MAX_PREVIEW_ROWS",
    "MAX_PREVIEW_TEXT",
    "build_preview",
]
