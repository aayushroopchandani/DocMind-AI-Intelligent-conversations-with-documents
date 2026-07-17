from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


TableColumnType = Literal["string", "number", "boolean", "date"]


class TableColumn(BaseModel):
    key: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)
    type: TableColumnType = "string"
    unit: str | None = None


class TableSourceFragment(BaseModel):
    page: int = Field(..., ge=1)
    bounding_box: list[float] = Field(..., min_length=4, max_length=4)


class StructuredTable(BaseModel):
    """A normalized table extracted from one or more PDF fragments."""

    id: str | None = Field(default=None, alias="_id")
    table_id: str = Field(..., min_length=1)
    document_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    chat_id: str | None = None
    node_id: str | None = None
    page_start: int = Field(..., ge=1)
    page_end: int = Field(..., ge=1)
    title: str = Field(..., min_length=1)
    columns: list[TableColumn] = Field(..., min_length=1)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    source_fragments: list[TableSourceFragment] = Field(..., min_length=1)

    # Stored with the normalized data so Qdrant can be rebuilt without making
    # another LLM call.
    short_summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    deterministic_summary: str = ""
    summary: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"populate_by_name": True}


class StructuredTableList(BaseModel):
    document_id: str
    tables: list[StructuredTable] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1)
