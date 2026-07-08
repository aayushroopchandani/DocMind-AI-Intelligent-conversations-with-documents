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

class CloudinaryPdf(BaseModel):
    public_id: str = Field(..., description="Cloudinary public identifier")
    # Cloudinary's globally-unique internal asset id (the "private" id).
    private_id: str = Field(default="", description="Cloudinary asset id (private)")
    secure_url: str = Field(default="", description="HTTPS delivery URL for the asset")
    resource_type: str = Field(default="image", description="Cloudinary resource type")
    filename: str = Field(default="", description="Original uploaded filename")
    bytes: Optional[int] = Field(default=None, description="File size in bytes")
    pages: Optional[int] = Field(default=None, description="Number of PDF pages")
    nodes: Optional[list[Node]] = Field(default=None, description="Nodes of the PDF")

class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"] = Field(..., description="Message author role")
    content: str = Field(..., description="Message content")
    created_at: datetime = Field(default_factory=utc_now)
    # Structured payload for assistant messages (citations, contributions,
    # confidence, follow-ups) so the frontend can re-render past answers fully.
    meta: Optional[dict] = Field(default=None, description="Structured response metadata")


class ChatMemory(BaseModel):
    """Rolling per-chat summary used for follow-up understanding."""

    summary: str = Field(default="", description="Compact summary of older messages")
    summarized_count: int = Field(
        default=0, description="How many conversation messages the summary covers"
    )
    updated_at: datetime = Field(default_factory=utc_now)


class ChatBase(BaseModel):
    pdf: list[CloudinaryPdf] = Field(
        default_factory=list,
        description="PDF asset metadata stored in Cloudinary",
    )
    conversation: list[ConversationMessage] = Field(
        default_factory=list,
        description="Conversation between AI and user",
    )
    memory: Optional[ChatMemory] = Field(
        default=None, description="Rolling summary memory for the chat"
    )


class ChatCreate(ChatBase):
    pass


class ChatInDB(ChatBase):
    id: Optional[str] = Field(default=None, alias="_id")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    user_id: str = Field(...,description="User ID")
    model_config = {"populate_by_name": True}
