from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserBase(BaseModel):
    email: str = Field(..., description="Unique user email")
    chats: list[str] = Field(
        default_factory=list,
        description="List of chat_ids owned by the user",
    )


class UserCreate(UserBase):
    pass


class UserInDB(UserBase):
    id: Optional[str] = Field(default=None, alias="_id")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"populate_by_name": True}
