"""
Thin data-access layer for MongoDB.

The FastAPI route handlers stay small and delegate all persistence logic here,
keeping business rules (ownership checks, PDF limits) in one testable place.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from config.settings import settings
from db.mongodb import get_db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(doc: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Convert a Mongo document's ObjectId `_id` into a string `id`."""
    if doc is None:
        return None
    doc = dict(doc)
    _id = doc.pop("_id", None)
    if _id is not None:
        doc["id"] = str(_id)
    return doc


def _as_object_id(value: str) -> Optional[ObjectId]:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
async def upsert_user(*, clerk_user_id: str, email: str) -> dict[str, Any]:
    """
    Idempotently ensure a user exists.

    Keyed on the stable Clerk user id. Called on sign-in; if the user already
    exists we only refresh the email + updated_at, so repeated logins do not
    create duplicates (this is the "check if already in the db" behavior).
    """
    db = get_db()
    now = _utc_now()

    try:
        doc = await db.users.find_one_and_update(
            {"clerk_user_id": clerk_user_id},
            {
                "$setOnInsert": {
                    "clerk_user_id": clerk_user_id,
                    "chats": [],
                    "created_at": now,
                },
                "$set": {"email": email, "updated_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        # Email unique-index collision (same email, different Clerk id). Fall
        # back to returning whatever already exists for this Clerk id.
        doc = await db.users.find_one({"clerk_user_id": clerk_user_id})

    return _serialize(doc)  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Chats
# --------------------------------------------------------------------------- #
async def create_chat(*, user_id: str) -> dict[str, Any]:
    """Create an empty chat owned by `user_id` (the Clerk user id)."""
    db = get_db()
    now = _utc_now()
    doc = {
        "user_id": user_id,
        "pdf": [],
        "conversation": [],
        "created_at": now,
        "updated_at": now,
    }
    result = await db.chats.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Keep a back-reference on the user for quick listing later.
    await db.users.update_one(
        {"clerk_user_id": user_id},
        {"$addToSet": {"chats": str(result.inserted_id)}},
    )
    return _serialize(doc)  # type: ignore[return-value]


async def get_chat(*, chat_id: str, user_id: str) -> Optional[dict[str, Any]]:
    """Fetch a chat only if it belongs to `user_id`."""
    oid = _as_object_id(chat_id)
    if oid is None:
        return None
    db = get_db()
    doc = await db.chats.find_one({"_id": oid, "user_id": user_id})
    return _serialize(doc)


async def add_pdf_to_chat(
    *, chat_id: str, user_id: str, pdf: dict[str, Any]
) -> dict[str, Any]:
    """
    Append a Cloudinary PDF reference to a chat.

    Raises ValueError for ownership/limit problems so the API layer can map
    them to appropriate HTTP status codes.
    """
    oid = _as_object_id(chat_id)
    if oid is None:
        raise ValueError("Chat not found")

    db = get_db()
    chat = await db.chats.find_one({"_id": oid, "user_id": user_id})
    if chat is None:
        raise ValueError("Chat not found")

    if len(chat.get("pdf", [])) >= settings.max_pdfs_per_chat:
        raise ValueError(
            f"You can upload a maximum of {settings.max_pdfs_per_chat} PDFs in one chat."
        )

    doc = await db.chats.find_one_and_update(
        {"_id": oid, "user_id": user_id},
        {"$push": {"pdf": pdf}, "$set": {"updated_at": _utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(doc)  # type: ignore[return-value]


async def append_conversation_messages(
    *, chat_id: str, user_id: str, messages: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """
    Append user/assistant messages to a chat's conversation in one write
    (called once per exchange after streaming completes — never per token).
    """
    oid = _as_object_id(chat_id)
    if oid is None:
        return None

    now = _utc_now()
    for message in messages:
        message.setdefault("created_at", now)

    db = get_db()
    doc = await db.chats.find_one_and_update(
        {"_id": oid, "user_id": user_id},
        {
            "$push": {"conversation": {"$each": messages}},
            "$set": {"updated_at": now},
        },
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(doc)


async def update_chat_memory(
    *, chat_id: str, user_id: str, summary: str, summarized_count: int
) -> None:
    """Persist the rolling conversation summary for a chat."""
    oid = _as_object_id(chat_id)
    if oid is None:
        return

    db = get_db()
    await db.chats.update_one(
        {"_id": oid, "user_id": user_id},
        {
            "$set": {
                "memory": {
                    "summary": summary,
                    "summarized_count": summarized_count,
                    "updated_at": _utc_now(),
                },
                "updated_at": _utc_now(),
            }
        },
    )


async def remove_pdf_from_chat(
    *, chat_id: str, user_id: str, public_id: str
) -> Optional[dict[str, Any]]:
    """Remove a PDF (by Cloudinary public_id) from a chat. Returns updated chat."""
    oid = _as_object_id(chat_id)
    if oid is None:
        return None

    db = get_db()
    doc = await db.chats.find_one_and_update(
        {"_id": oid, "user_id": user_id},
        {"$pull": {"pdf": {"public_id": public_id}}, "$set": {"updated_at": _utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(doc)
