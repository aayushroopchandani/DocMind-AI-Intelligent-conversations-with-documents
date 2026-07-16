"""
Thin data-access layer for MongoDB.

The FastAPI route handlers stay small and delegate all persistence logic here,
keeping business rules (ownership checks, PDF limits) in one testable place.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from config.settings import settings
from db.models.attempt_quiz import QuizAttemptCreate
from db.models.generated_quiz import GeneratedQuizCreate
from db.mongodb import get_db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(doc: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Convert Mongo ObjectIds into JSON-safe strings."""
    if doc is None:
        return None
    doc = dict(doc)
    _id = doc.pop("_id", None)
    if _id is not None:
        doc["id"] = str(_id)

    for field in ("doc_ids", "chat_ids", "chats"):
        if isinstance(doc.get(field), list):
            doc[field] = [str(value) for value in doc[field]]

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
        "doc_ids": [],
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


async def get_user_chats(*, user_id: str) -> Optional[list[dict[str, Any]]]:
    """Populate a user's chat id references into chat documents."""
    db = get_db()
    user = await db.users.find_one({"clerk_user_id": user_id}, {"chats": 1})
    if user is None:
        return None

    chat_ids = user.get("chats", [])
    object_ids = [_as_object_id(chat_id) for chat_id in chat_ids]
    valid_ids = [oid for oid in object_ids if oid is not None]
    if not valid_ids:
        return []

    chats = await db.chats.find(
        {"_id": {"$in": valid_ids}, "user_id": user_id}
    ).to_list(length=len(valid_ids))
    by_id = {chat["_id"]: chat for chat in chats}

    return [
        _serialize(by_id[oid])  # type: ignore[misc]
        for oid in valid_ids
        if oid in by_id
    ]


async def get_documents_by_ids(
    *, document_ids: list[str], user_id: str
) -> list[dict[str, Any]]:
    """Fetch documents in the same order as the supplied MongoDB IDs."""
    object_ids = [_as_object_id(value) for value in document_ids]
    valid_ids = [value for value in object_ids if value is not None]
    if not valid_ids:
        return []

    db = get_db()
    documents = await db.documents.find(
        {"_id": {"$in": valid_ids}, "user_id": user_id}
    ).to_list(length=len(valid_ids))
    by_id = {document["_id"]: document for document in documents}
    return [
        _serialize(by_id[oid])  # type: ignore[misc]
        for oid in valid_ids
        if oid in by_id
    ]


async def get_ready_document(
    *, user_id: str, document_id: str
) -> Optional[dict[str, Any]]:
    db = get_db()
    document = await db.documents.find_one(
        {
            "user_id": user_id,
            "document_id": document_id,
            "ingestion_status": "ready",
        }
    )
    return _serialize(document)


async def get_nodes_ingestion_status(
    *, user_id: str, document_id: str
) -> Optional[str]:
    """Return the node-vector ingestion status for a user's document hash."""
    db = get_db()
    document = await db.documents.find_one(
        {"user_id": user_id, "document_id": document_id},
        {"nodes.ingestion_status": 1},
    )
    if document is None:
        return None

    nodes = document.get("nodes") or {}
    if isinstance(nodes, dict) and nodes.get("ingestion_status") == "ready":
        return "ready"
    return "not_ready"


async def get_document_nodes(
    *, user_id: str, document_id: str
) -> Optional[dict[str, Any]]:
    """Fetch outline and summary-index metadata for one user's document hash."""
    db = get_db()
    document = await db.documents.find_one(
        {"user_id": user_id, "document_id": document_id},
        {
            "_id": 0,
            "user_id": 1,
            "document_id": 1,
            "nodes": 1,
            "summary_index_status": 1,
            "summary_index_version": 1,
        },
    )
    if document is None:
        return None

    nodes_data = document.get("nodes") or {}
    if isinstance(nodes_data, list):
        nodes_data = {"nodes": nodes_data, "ingestion_status": "not_ready"}
    elif not isinstance(nodes_data, dict):
        nodes_data = {"nodes": None, "ingestion_status": "not_ready"}

    nodes_data.setdefault("nodes", None)
    nodes_data.setdefault("ingestion_status", "not_ready")
    document["nodes"] = nodes_data
    return document


async def claim_summary_index_build(
    *, user_id: str, document_id: str, version: str
) -> bool:
    """Atomically claim a pending, failed, missing, or outdated index build."""
    db = get_db()
    document = await db.documents.find_one_and_update(
        {
            "user_id": user_id,
            "document_id": document_id,
            "ingestion_status": "ready",
            "$or": [
                {"summary_index_version": {"$ne": version}},
                {"summary_index_status": {"$in": ["pending", "failed"]}},
                {"summary_index_status": {"$exists": False}},
            ],
        },
        {
            "$set": {
                "summary_index_status": "processing",
                "summary_index_version": version,
                "updated_at": _utc_now(),
            },
            "$unset": {"summary_index_error": ""},
        },
        return_document=ReturnDocument.AFTER,
    )
    return document is not None


async def complete_summary_index_build(
    *,
    user_id: str,
    document_id: str,
    version: str,
    nodes: list[dict[str, Any]],
) -> bool:
    """Persist representative IDs and mark the claimed build ready atomically."""
    db = get_db()
    result = await db.documents.update_one(
        {
            "user_id": user_id,
            "document_id": document_id,
            "summary_index_status": "processing",
            "summary_index_version": version,
        },
        {
            "$set": {
                "nodes.nodes": nodes,
                "summary_index_status": "ready",
                "summary_index_version": version,
                "updated_at": _utc_now(),
            },
            "$unset": {"summary_index_error": ""},
        },
    )
    return result.matched_count == 1


async def mark_summary_index_failed(
    *,
    user_id: str,
    document_id: str,
    version: str,
    error: str,
) -> bool:
    """Record a failed build without exposing its diagnostic to API clients."""
    db = get_db()
    result = await db.documents.update_one(
        {
            "user_id": user_id,
            "document_id": document_id,
            "summary_index_status": "processing",
            "summary_index_version": version,
        },
        {
            "$set": {
                "summary_index_status": "failed",
                "summary_index_error": error[:1000],
                "updated_at": _utc_now(),
            }
        },
    )
    return result.matched_count == 1


async def user_has_deep_summary_access(*, user_id: str) -> bool:
    """Return server-side entitlement for the monetized full hierarchy mode."""
    db = get_db()
    user = await db.users.find_one(
        {"clerk_user_id": user_id},
        {
            "plan": 1,
            "features.deep_summary": 1,
            "entitlements.deep_summary": 1,
        },
    )
    if user is None:
        return False

    features = user.get("features") or {}
    entitlements = user.get("entitlements") or {}
    return bool(
        features.get("deep_summary") is True
        or entitlements.get("deep_summary") is True
        or str(user.get("plan", "")).lower() in {"pro", "premium"}
    )


async def get_cached_summary(
    *,
    user_id: str,
    document_id: str,
    scope_root_node_id: str,
    mode: str,
    summary_index_version: str,
    prompt_version: str,
    model: str,
) -> Optional[dict[str, Any]]:
    """Fetch an exact versioned root-scope summary and record its cache hit."""
    db = get_db()
    now = _utc_now()
    document = await db.summary_cache.find_one_and_update(
        {
            "user_id": user_id,
            "document_id": document_id,
            "scope_root_node_id": scope_root_node_id,
            "mode": mode,
            "summary_index_version": summary_index_version,
            "prompt_version": prompt_version,
            "model": model,
            "status": "ready",
        },
        {
            "$set": {"last_accessed_at": now},
            "$inc": {"hit_count": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(document)


async def upsert_cached_summary(
    *,
    user_id: str,
    document_id: str,
    scope_root_node_id: str,
    scope_node_ids: list[str],
    mode: str,
    summary_index_version: str,
    prompt_version: str,
    model: str,
    response: dict[str, Any],
    metrics: dict[str, Any],
) -> None:
    """Persist one canonical summary response for an exact requested scope."""
    db = get_db()
    now = _utc_now()
    key = {
        "user_id": user_id,
        "document_id": document_id,
        "scope_root_node_id": scope_root_node_id,
        "mode": mode,
        "summary_index_version": summary_index_version,
        "prompt_version": prompt_version,
        "model": model,
    }
    await db.summary_cache.update_one(
        key,
        {
            "$setOnInsert": {
                **key,
                "created_at": now,
                "hit_count": 0,
            },
            "$set": {
                "scope_node_ids": scope_node_ids,
                "status": "ready",
                "response": response,
                "metrics": metrics,
                "last_accessed_at": now,
                "updated_at": now,
            },
        },
        upsert=True,
    )


async def mark_nodes_ingestion_ready(*, user_id: str, document_id: str) -> bool:
    """Mark node-vector ingestion complete for a user's document hash."""
    db = get_db()
    result = await db.documents.update_one(
        {"user_id": user_id, "document_id": document_id},
        {
            "$set": {
                "nodes.ingestion_status": "ready",
                "updated_at": _utc_now(),
            }
        },
    )
    return result.matched_count == 1


async def create_pending_document(
    *, user_id: str, document_id: str, filename: str
) -> tuple[dict[str, Any], bool]:
    """Claim a content hash for ingestion; only one concurrent upload wins."""
    db = get_db()
    now = _utc_now()
    document = {
        "user_id": user_id,
        "document_id": document_id,
        "filename": filename,
        "chat_ids": [],
        "ingestion_status": "not_ready",
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = await db.documents.insert_one(document)
        document["_id"] = result.inserted_id
        return _serialize(document), True  # type: ignore[return-value]
    except DuplicateKeyError:
        existing = await db.documents.find_one(
            {"user_id": user_id, "document_id": document_id}
        )
        if existing is None:
            return await create_pending_document(
                user_id=user_id,
                document_id=document_id,
                filename=filename,
            )
        return _serialize(existing), False  # type: ignore[return-value]


async def mark_document_ready(
    *, document_db_id: str, user_id: str, metadata: dict[str, Any]
) -> dict[str, Any]:
    document_oid = _as_object_id(document_db_id)
    if document_oid is None:
        raise ValueError("Document not found")

    db = get_db()
    document = await db.documents.find_one_and_update(
        {"_id": document_oid, "user_id": user_id},
        {
            "$set": {
                **metadata,
                "ingestion_status": "ready",
                "updated_at": _utc_now(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        raise ValueError("Document not found")
    return _serialize(document)  # type: ignore[return-value]


async def discard_pending_document(*, document_db_id: str, user_id: str) -> None:
    document_oid = _as_object_id(document_db_id)
    if document_oid is None:
        return
    db = get_db()
    await db.documents.delete_one(
        {
            "_id": document_oid,
            "user_id": user_id,
            "ingestion_status": "not_ready",
        }
    )


async def attach_document_to_chat(
    *, chat_id: str, user_id: str, document_db_id: str
) -> dict[str, Any]:
    chat_oid = _as_object_id(chat_id)
    document_oid = _as_object_id(document_db_id)
    if chat_oid is None or document_oid is None:
        raise ValueError("Chat or document not found")

    db = get_db()
    chat = await db.chats.find_one({"_id": chat_oid, "user_id": user_id})
    document = await db.documents.find_one(
        {"_id": document_oid, "user_id": user_id, "ingestion_status": "ready"}
    )
    if chat is None or document is None:
        raise ValueError("Chat or document not found")

    doc_ids = chat.get("doc_ids", [])
    if document_oid not in doc_ids and len(doc_ids) >= settings.max_pdfs_per_chat:
        raise ValueError(
            f"You can upload a maximum of {settings.max_pdfs_per_chat} PDFs in one chat."
        )

    now = _utc_now()
    chat = await db.chats.find_one_and_update(
        {"_id": chat_oid, "user_id": user_id},
        {"$addToSet": {"doc_ids": document_oid}, "$set": {"updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    await db.documents.update_one(
        {"_id": document_oid, "user_id": user_id},
        {"$addToSet": {"chat_ids": chat_oid}, "$set": {"updated_at": now}},
    )
    return _serialize(chat)  # type: ignore[return-value]


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
        if not message.get("id"):
            message["id"] = str(uuid4())
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


async def append_conversation_message_if_missing(
    *, chat_id: str, user_id: str, message: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """Append a stable-id message once, making quiz retries idempotent."""
    oid = _as_object_id(chat_id)
    message_id = message.get("id")
    if oid is None or not message_id:
        return None

    now = _utc_now()
    stored_message = dict(message)
    stored_message.setdefault("created_at", now)

    db = get_db()
    doc = await db.chats.find_one_and_update(
        {
            "_id": oid,
            "user_id": user_id,
            "conversation.id": {"$ne": message_id},
        },
        {
            "$push": {"conversation": stored_message},
            "$set": {"updated_at": now},
        },
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        doc = await db.chats.find_one({"_id": oid, "user_id": user_id})
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


# --------------------------------------------------------------------------- #
# Generated quizzes
# --------------------------------------------------------------------------- #
async def get_generated_quiz(
    *, quiz_id: str, user_id: str
) -> Optional[dict[str, Any]]:
    """Fetch an evaluable generated quiz owned by the authenticated user."""
    oid = _as_object_id(quiz_id)
    if oid is None:
        return None
    db = get_db()
    document = await db.generated_quiz.find_one(
        {"_id": oid, "user_id": user_id, "status": "generated"}
    )
    return _serialize(document)


async def get_generated_quiz_by_source_message(
    *, chat_id: str, user_id: str, source_message_id: str
) -> Optional[dict[str, Any]]:
    """Find a generated quiz for an originating chat message."""
    db = get_db()
    document = await db.generated_quiz.find_one(
        {
            "chat_id": chat_id,
            "user_id": user_id,
            "source_message_id": source_message_id,
            "status": "generated",
        }
    )
    return _serialize(document)


async def create_generated_quiz(*, quiz: GeneratedQuizCreate) -> dict[str, Any]:
    """Persist one generated quiz document and return the serialized record."""
    db = get_db()
    now = _utc_now()
    document = quiz.model_dump(mode="python")
    document["created_at"] = now
    document["updated_at"] = now

    result = await db.generated_quiz.insert_one(document)
    document["_id"] = result.inserted_id
    return _serialize(document)  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Quiz attempts
# --------------------------------------------------------------------------- #
def _attempt_submission_signature(answers: list[Any]) -> list[dict[str, Any]]:
    signature: list[dict[str, Any]] = []
    for answer in answers:
        value = (
            answer.model_dump(mode="python")
            if hasattr(answer, "model_dump")
            else answer
        )
        signature.append(
            {
                "question_id": value.get("question_id"),
                "response": value.get("response", {}),
                "time_taken_seconds": value.get("time_taken_seconds", 0),
            }
        )
    return signature


def _ensure_same_attempt_submission(
    existing: dict[str, Any], attempt: QuizAttemptCreate
) -> None:
    if _attempt_submission_signature(existing.get("answers", [])) != (
        _attempt_submission_signature(attempt.answers)
    ):
        raise ValueError(
            "This submission id was already used with different quiz answers."
        )


async def create_quiz_attempt(*, attempt: QuizAttemptCreate) -> dict[str, Any]:
    """Persist an evaluated quiz attempt with a race-safe attempt number."""
    db = get_db()
    now = _utc_now()
    document = attempt.model_dump(mode="python")
    document["created_at"] = now
    document["updated_at"] = now

    if attempt.submission_id:
        existing = await db.quiz_attempts.find_one(
            {
                "quiz_id": attempt.quiz_id,
                "user_id": attempt.user_id,
                "submission_id": attempt.submission_id,
            }
        )
        if existing is not None:
            _ensure_same_attempt_submission(existing, attempt)
            return _serialize(existing)  # type: ignore[return-value]

    for _ in range(5):
        latest = await db.quiz_attempts.find_one(
            {"quiz_id": attempt.quiz_id, "user_id": attempt.user_id},
            {"attempt_number": 1},
            sort=[("attempt_number", -1)],
        )
        candidate = dict(document)
        candidate["attempt_number"] = (
            int(latest.get("attempt_number", 0)) + 1 if latest else 1
        )
        try:
            result = await db.quiz_attempts.insert_one(candidate)
        except DuplicateKeyError:
            if attempt.submission_id:
                existing = await db.quiz_attempts.find_one(
                    {
                        "quiz_id": attempt.quiz_id,
                        "user_id": attempt.user_id,
                        "submission_id": attempt.submission_id,
                    }
                )
                if existing is not None:
                    _ensure_same_attempt_submission(existing, attempt)
                    return _serialize(existing)  # type: ignore[return-value]
            continue
        candidate["_id"] = result.inserted_id
        return _serialize(candidate)  # type: ignore[return-value]

    raise RuntimeError("Could not allocate a quiz attempt number. Please retry.")


async def detach_document_from_chat(
    *, chat_id: str, user_id: str, document_db_id: str
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    chat_oid = _as_object_id(chat_id)
    document_oid = _as_object_id(document_db_id)
    if chat_oid is None or document_oid is None:
        return None, None

    db = get_db()
    now = _utc_now()
    chat = await db.chats.find_one_and_update(
        {"_id": chat_oid, "user_id": user_id, "doc_ids": document_oid},
        {"$pull": {"doc_ids": document_oid}, "$set": {"updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if chat is None:
        return None, None

    document = await db.documents.find_one_and_update(
        {"_id": document_oid, "user_id": user_id},
        {"$pull": {"chat_ids": chat_oid}, "$set": {"updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(chat), _serialize(document)


async def delete_orphan_document(*, document_db_id: str, user_id: str) -> bool:
    document_oid = _as_object_id(document_db_id)
    if document_oid is None:
        return False
    db = get_db()
    document = await db.documents.find_one(
        {"_id": document_oid, "user_id": user_id},
        {"document_id": 1},
    )
    if document is None:
        return False
    result = await db.documents.delete_one(
        {"_id": document_oid, "user_id": user_id, "chat_ids": {"$size": 0}}
    )
    if result.deleted_count == 1 and document.get("document_id"):
        await db.summary_cache.delete_many(
            {"user_id": user_id, "document_id": document["document_id"]}
        )
    return result.deleted_count == 1
