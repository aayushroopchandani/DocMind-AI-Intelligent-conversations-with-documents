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
    """Ensure indexes for User/Chat collections."""
    db = get_db()
    await db.users.create_index("email", unique=True)
    await db.users.create_index("clerk_user_id", unique=True)
    await db.chats.create_index("user_id")
    await db.chats.create_index("created_at")
    await db.chats.create_index("updated_at")
