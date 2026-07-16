from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConversationMessage(BaseModel):
    id: Optional[str] = Field(
        default=None,
        description="Stable backend-generated message ID",
    )
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
    doc_ids: list[str] = Field(
        default_factory=list,
        description="MongoDB document IDs attached to this chat",
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
