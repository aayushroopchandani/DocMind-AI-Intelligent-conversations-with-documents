"""Read-only Phase-8 MongoDB index verification for CI and deployment checks."""

from __future__ import annotations

import asyncio

from config.settings import settings
from db.indexes import verify_analysis_indexes

try:
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError:  # pragma: no cover - dependency validation
    AsyncIOMotorClient = None  # type: ignore[assignment]


async def _verify() -> int:
    if not settings.mongodb_is_configured:
        print("MongoDB is not configured.")
        return 2
    if AsyncIOMotorClient is None:
        print("motor is not installed.")
        return 2

    client = AsyncIOMotorClient(settings.mongodb_uri)
    try:
        try:
            database = client[settings.mongodb_db_name]
            await database.command("ping")
            report = await verify_analysis_indexes(database)
        except Exception as exc:
            print(
                "Phase-8 MongoDB index verification failed: "
                f"{type(exc).__name__}."
            )
            return 2
    finally:
        client.close()

    if report.ok:
        print("Phase-8 MongoDB indexes match the declared contract.")
        return 0
    for item in report.drift:
        print(f"{item.collection_name}.{item.index_name}: {item.reason}")
    return 1


def main() -> None:
    raise SystemExit(asyncio.run(_verify()))


if __name__ == "__main__":
    main()
