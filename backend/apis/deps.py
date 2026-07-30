"""Shared FastAPI dependencies for authenticated internal endpoints."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from config.settings import settings


async def verify_internal_secret(
    x_internal_secret: str | None = Header(default=None),
) -> None:
    """
    Guard endpoints that are only meant to be called by our Next.js server.

    The Next.js route handlers verify the Clerk session, then forward requests
    with this shared secret. The backend must fail closed when the trust secret
    is absent: otherwise an internet client could choose any ``X-User-ID`` and
    cross the tenant boundary.
    """
    expected = settings.internal_api_secret
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Internal API authentication is not configured",
        )
    supplied = x_internal_secret or ""
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid internal secret")


async def current_user_id(x_user_id: str | None = Header(default=None)) -> str:
    """
    The authenticated Clerk user id, injected by the Next.js proxy as a header
    after it verifies the session server-side.
    """
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user identity")
    return x_user_id
