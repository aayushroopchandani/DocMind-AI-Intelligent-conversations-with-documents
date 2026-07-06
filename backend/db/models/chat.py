from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CloudinaryPdf(BaseModel):
    public_id: str = Field(..., description="Cloudinary public identifier")
    private_id: str = Field(..., description="Cloudinary private identifier")


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"] = Field(..., description="Message author role")
    content: str = Field(..., description="Message content")
    created_at: datetime = Field(default_factory=utc_now)


class ChatBase(BaseModel):
    pdf: list[CloudinaryPdf] = Field(
        default_factory=list,
        description="PDF asset metadata stored in Cloudinary",
    )
    conversation: list[ConversationMessage] = Field(
        default_factory=list,
        description="Conversation between AI and user",
    )


class ChatCreate(ChatBase):
    pass


class ChatInDB(ChatBase):
    id: Optional[str] = Field(default=None, alias="_id")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"populate_by_name": True}
