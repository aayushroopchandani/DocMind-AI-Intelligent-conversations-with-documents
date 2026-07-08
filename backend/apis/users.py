"""User sync endpoint — mirrors a Clerk user into MongoDB on sign-in."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apis.deps import verify_internal_secret
from db import crud

router = APIRouter(prefix="/users", tags=["users"])


class SyncUserRequest(BaseModel):
    clerk_user_id: str = Field(..., description="Stable Clerk user id")
    email: str = Field(..., description="User's primary email")


class SyncUserResponse(BaseModel):
    id: str
    clerk_user_id: str
    email: str


@router.post("/sync", response_model=SyncUserResponse)
async def sync_user(
    body: SyncUserRequest,
    _: None = Depends(verify_internal_secret),
) -> SyncUserResponse:
    """
    Idempotently create the user if they don't already exist.

    Safe to call on every sign-in: existing users are matched by Clerk id and
    only have their email/timestamp refreshed, never duplicated.
    """
    try:
        user = await crud.upsert_user(
            clerk_user_id=body.clerk_user_id, email=body.email
        )
    except RuntimeError as exc:
        # MongoDB not initialized/configured.
        raise HTTPException(status_code=503, detail=str(exc))

    return SyncUserResponse(
        id=user["id"],
        clerk_user_id=user["clerk_user_id"],
        email=user["email"],
    )
