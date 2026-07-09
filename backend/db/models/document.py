from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Node(BaseModel):
    node_id: str = Field(..., description="Node ID")
    title: str = Field(..., description="Node title")
    level: int = Field(..., description="Node level")
    page_start: int = Field(..., description="Page start")
    page_end: int = Field(..., description="Page end")
    parent_id: Optional[str] = Field(default=None, description="Parent node ID")
    normalized_title: Optional[str] = Field(
        default=None, description="Normalized title used for node search"
    )

class NodeData(BaseModel):
    nodes: Optional[list[Node]] = Field(default=None, description="Nodes")
    ingestion_status: Literal["ready", "not_ready"] = "not_ready"

class PdfDocument(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    document_id: str = Field(..., description="SHA-256 hash of the PDF content")
    user_id: str
    chat_ids: list[str] = Field(default_factory=list)
    ingestion_status: Literal["ready", "not_ready"] = "not_ready"
    public_id: str = ""
    private_id: str = ""
    secure_url: str = ""
    resource_type: str = "raw"
    filename: str = ""
    bytes: Optional[int] = None
    pages: Optional[int] = None
    nodes: NodeData = Field(default_factory=NodeData)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"populate_by_name": True}
