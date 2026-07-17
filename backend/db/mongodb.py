from __future__ import annotations

import logging
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

try:
    from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
except ImportError:  # pragma: no cover
    AsyncIOMotorClient = None  # type: ignore[assignment]
    AsyncIOMotorDatabase = None  # type: ignore[assignment]


_mongo_client: Optional["AsyncIOMotorClient"] = None
_mongo_db: Optional["AsyncIOMotorDatabase"] = None


async def init_mongodb() -> None:
    """Initialize MongoDB client and ensure basic indexes exist."""
    global _mongo_client, _mongo_db

    if not settings.mongodb_is_configured:
        logger.warning(
            "MongoDB is not configured. Set MONGODB_URI and MONGODB_DB_NAME to enable it."
        )
        return

    if AsyncIOMotorClient is None:
        logger.warning(
            "motor is not installed. Run: pip install motor pymongo"
        )
        return

    _mongo_client = AsyncIOMotorClient(settings.mongodb_uri)
    _mongo_db = _mongo_client[settings.mongodb_db_name]

    await _mongo_db.command("ping")
    await ensure_indexes()
    logger.info("MongoDB connected: db=%s", settings.mongodb_db_name)


async def close_mongodb() -> None:
    global _mongo_client, _mongo_db
    if _mongo_client is not None:
        _mongo_client.close()
    _mongo_client = None
    _mongo_db = None


def get_db() -> "AsyncIOMotorDatabase":
    if _mongo_db is None:
        raise RuntimeError("MongoDB is not initialized. Call init_mongodb() first.")
    return _mongo_db


async def ensure_indexes() -> None:
    """Ensure indexes for the application's MongoDB collections."""
    db = get_db()
    await db.users.create_index("email", unique=True)
    await db.users.create_index("clerk_user_id", unique=True)
    await db.chats.create_index("user_id")
    await db.chats.create_index("created_at")
    await db.chats.create_index("updated_at")
    await db.documents.create_index(
        [("user_id", 1), ("document_id", 1)], unique=True
    )
    await db.documents.create_index("chat_ids")
    await db.documents.create_index("ingestion_status")
    await db.documents.create_index("summary_index_status")
    await db.documents.create_index("table_ingestion_status")
    await db.structured_tables.create_index(
        [("user_id", 1), ("table_id", 1)], unique=True
    )
    await db.structured_tables.create_index(
        [("user_id", 1), ("document_id", 1), ("page_start", 1)]
    )
    await db.structured_tables.create_index("node_id")
    await db.structured_tables.create_index("chat_id")
    await db.summary_cache.create_index(
        [
            ("user_id", 1),
            ("document_id", 1),
            ("scope_root_node_id", 1),
            ("mode", 1),
            ("summary_index_version", 1),
            ("prompt_version", 1),
            ("model", 1),
        ],
        unique=True,
    )
    await db.summary_cache.create_index("last_accessed_at")
    await db.generated_quiz.create_index("user_id")
    await db.generated_quiz.create_index("chat_id")
    await db.generated_quiz.create_index([("user_id", 1), ("chat_id", 1)])
    await db.generated_quiz.create_index(
        [("user_id", 1), ("chat_id", 1), ("source_message_id", 1)],
        unique=True,
        partialFilterExpression={"source_message_id": {"$type": "string"}},
    )
    await db.generated_quiz.create_index("doc_ids")
    await db.generated_quiz.create_index("status")
    await db.generated_quiz.create_index("created_at")
    await db.generated_quiz.create_index("updated_at")
    await db.quiz_attempts.create_index("quiz_id")
    await db.quiz_attempts.create_index("chat_id")
    await db.quiz_attempts.create_index([("user_id", 1), ("created_at", -1)])
    await db.quiz_attempts.create_index(
        [("user_id", 1), ("quiz_id", 1), ("attempt_number", 1)],
        unique=True,
    )
    await db.quiz_attempts.create_index(
        [("user_id", 1), ("quiz_id", 1), ("submission_id", 1)],
        unique=True,
        partialFilterExpression={"submission_id": {"$type": "string"}},
    )
    await db.quiz_attempts.create_index("weak_topics.main_topic")
