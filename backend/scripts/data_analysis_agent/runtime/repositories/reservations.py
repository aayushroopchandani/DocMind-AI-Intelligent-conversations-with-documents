"""Durable storage for exact rectangle write reservations (Phase 9.11.5).

The invariant — no two live reservations on one worksheet may overlap — has no
index that can express it, so it is enforced by reading intersecting rectangles
and inserting inside one transaction. Every query is an interval comparison on
four integers, which a compound index answers directly; nothing here scans a
sheet or a workspace.

Re-reserving the same patch revision is idempotent, and a *newer* revision of
the same patch supersedes its predecessor rather than colliding with it. Without
that, a rebase would deadlock against the reservation it just replaced.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from db.mongodb import get_db

from ..models.reservations import (
    ReservationStatus,
    SpatialReservation,
    utc_now,
)


class SpatialReservationError(RuntimeError):
    """Reservation persistence failed."""


class SpatialReservationConflictError(SpatialReservationError):
    """The rectangle overlaps a reservation another run already holds."""

    def __init__(
        self,
        message: str,
        *,
        conflicts: tuple[SpatialReservation, ...] = (),
    ) -> None:
        super().__init__(message)
        self.conflicts = conflicts


class SpatialReservationRepository(Protocol):
    async def reserve(
        self,
        reservation: SpatialReservation,
    ) -> SpatialReservation: ...

    async def list_active(
        self,
        *,
        user_id: str,
        workbook_id: str,
        worksheet_id: str | None = None,
        exclude_run_id: str | None = None,
        now: datetime | None = None,
        limit: int = 500,
    ) -> tuple[SpatialReservation, ...]: ...

    async def release(
        self,
        *,
        user_id: str,
        reservation_id: str,
        status: ReservationStatus,
        reason: str | None = None,
    ) -> SpatialReservation | None: ...

    async def release_for_run(
        self,
        *,
        user_id: str,
        run_id: str,
        status: ReservationStatus,
        reason: str | None = None,
        keep_reservation_id: str | None = None,
    ) -> int: ...

    async def expire_due(self, *, now: datetime | None = None) -> int: ...


def _as_utc(value: datetime) -> datetime:
    return (
        value.astimezone(timezone.utc)
        if value.tzinfo is not None
        else value.replace(tzinfo=timezone.utc)
    )


def _intersection_filter(reservation: SpatialReservation) -> dict[str, Any]:
    """Return the query matching every rectangle that overlaps this one.

    Two rectangles intersect exactly when each one starts before the other ends,
    on both axes. Four range comparisons, answered from the compound index.
    """

    return {
        "first_row": {"$lte": reservation.last_row},
        "last_row": {"$gte": reservation.first_row},
        "first_column": {"$lte": reservation.last_column},
        "last_column": {"$gte": reservation.first_column},
    }


class MongoSpatialReservationRepository:
    collection_name = "analysis_write_reservations"

    def __init__(self, database: Any | None = None) -> None:
        self._database = database

    def _db(self) -> Any:
        return self._database if self._database is not None else get_db()

    async def _in_transaction(
        self,
        callback: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        try:
            session_context = await self._db().client.start_session()
            async with session_context as session:
                return await session.with_transaction(callback)
        except SpatialReservationError:
            raise
        except PyMongoError as error:
            raise SpatialReservationError(
                "write reservation transaction failed; MongoDB transaction "
                "support is required"
            ) from error

    async def reserve(
        self,
        reservation: SpatialReservation,
    ) -> SpatialReservation:
        collection = self._db()[self.collection_name]
        now = _as_utc(reservation.created_at)

        async def transaction(session: Any) -> SpatialReservation:
            existing = await collection.find(
                {
                    "user_id": reservation.user_id,
                    "run_id": reservation.run_id,
                    "patch_id": reservation.patch_id,
                    "status": ReservationStatus.ACTIVE.value,
                },
                session=session,
            ).to_list(length=16)
            for document in existing:
                current = SpatialReservation.model_validate(document)
                if (
                    current.patch_revision == reservation.patch_revision
                    and current.rect == reservation.rect
                    and current.worksheet_id == reservation.worksheet_id
                ):
                    return current
                if current.patch_revision > reservation.patch_revision:
                    raise SpatialReservationConflictError(
                        "a newer revision of this patch already holds a "
                        "reservation",
                        conflicts=(current,),
                    )
            if existing:
                # This revision supersedes its own predecessors, so they are
                # released here rather than colliding with the insert below.
                await collection.update_many(
                    {
                        "user_id": reservation.user_id,
                        "run_id": reservation.run_id,
                        "patch_id": reservation.patch_id,
                        "status": ReservationStatus.ACTIVE.value,
                    },
                    {
                        "$set": {
                            "status": ReservationStatus.RELEASED.value,
                            "released_reason": "superseded_by_patch_revision",
                            "updated_at": now,
                        }
                    },
                    session=session,
                )
            conflicts = await collection.find(
                {
                    "user_id": reservation.user_id,
                    "workbook_id": reservation.workbook_id,
                    "worksheet_id": reservation.worksheet_id,
                    "status": ReservationStatus.ACTIVE.value,
                    "expires_at": {"$gt": now},
                    "patch_id": {"$ne": reservation.patch_id},
                    **_intersection_filter(reservation),
                },
                session=session,
            ).to_list(length=16)
            if conflicts:
                raise SpatialReservationConflictError(
                    "the target rectangle overlaps an active reservation",
                    conflicts=tuple(
                        SpatialReservation.model_validate(document)
                        for document in conflicts
                    ),
                )
            await collection.insert_one(
                reservation.model_dump(mode="python"),
                session=session,
            )
            return reservation

        try:
            return await self._in_transaction(transaction)
        except DuplicateKeyError as error:
            raise SpatialReservationConflictError(
                "this patch revision is already reserved"
            ) from error

    async def list_active(
        self,
        *,
        user_id: str,
        workbook_id: str,
        worksheet_id: str | None = None,
        exclude_run_id: str | None = None,
        now: datetime | None = None,
        limit: int = 500,
    ) -> tuple[SpatialReservation, ...]:
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        query: dict[str, Any] = {
            "user_id": user_id,
            "workbook_id": workbook_id,
            "status": ReservationStatus.ACTIVE.value,
            "expires_at": {"$gt": _as_utc(now or utc_now())},
        }
        if worksheet_id is not None:
            query["worksheet_id"] = worksheet_id
        if exclude_run_id is not None:
            query["run_id"] = {"$ne": exclude_run_id}
        try:
            documents = await self._db()[self.collection_name].find(
                query
            ).to_list(length=limit)
        except PyMongoError as error:
            raise SpatialReservationError(
                "write reservations could not be read"
            ) from error
        return tuple(
            SpatialReservation.model_validate(document) for document in documents
        )

    async def release(
        self,
        *,
        user_id: str,
        reservation_id: str,
        status: ReservationStatus,
        reason: str | None = None,
    ) -> SpatialReservation | None:
        if status is ReservationStatus.ACTIVE:
            raise ValueError("release requires a terminal reservation status")
        try:
            document = await self._db()[
                self.collection_name
            ].find_one_and_update(
                {
                    "user_id": user_id,
                    "reservation_id": reservation_id,
                    "status": ReservationStatus.ACTIVE.value,
                },
                {
                    "$set": {
                        "status": status.value,
                        "released_reason": reason,
                        "updated_at": utc_now(),
                    },
                    "$inc": {"version": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as error:
            raise SpatialReservationError(
                "write reservation could not be released"
            ) from error
        return (
            SpatialReservation.model_validate(document)
            if document is not None
            else None
        )

    async def release_for_run(
        self,
        *,
        user_id: str,
        run_id: str,
        status: ReservationStatus,
        reason: str | None = None,
        keep_reservation_id: str | None = None,
    ) -> int:
        if status is ReservationStatus.ACTIVE:
            raise ValueError("release requires a terminal reservation status")
        query: dict[str, Any] = {
            "user_id": user_id,
            "run_id": run_id,
            "status": ReservationStatus.ACTIVE.value,
        }
        if keep_reservation_id is not None:
            query["reservation_id"] = {"$ne": keep_reservation_id}
        try:
            result = await self._db()[self.collection_name].update_many(
                query,
                {
                    "$set": {
                        "status": status.value,
                        "released_reason": reason,
                        "updated_at": utc_now(),
                    },
                    "$inc": {"version": 1},
                },
            )
        except PyMongoError as error:
            raise SpatialReservationError(
                "write reservations could not be released"
            ) from error
        return int(result.modified_count)

    async def expire_due(self, *, now: datetime | None = None) -> int:
        moment = _as_utc(now or utc_now())
        try:
            result = await self._db()[self.collection_name].update_many(
                {
                    "status": ReservationStatus.ACTIVE.value,
                    "expires_at": {"$lte": moment},
                },
                {
                    "$set": {
                        "status": ReservationStatus.EXPIRED.value,
                        "released_reason": "lease_expired",
                        "updated_at": moment,
                    },
                    "$inc": {"version": 1},
                },
            )
        except PyMongoError as error:
            raise SpatialReservationError(
                "expired write reservations could not be swept"
            ) from error
        return int(result.modified_count)


class InMemorySpatialReservationRepository:
    """Process-local store with the same overlap semantics as the Mongo one."""

    def __init__(self) -> None:
        self._entries: dict[str, SpatialReservation] = {}

    async def reserve(
        self,
        reservation: SpatialReservation,
    ) -> SpatialReservation:
        now = _as_utc(reservation.created_at)
        own = [
            item
            for item in self._entries.values()
            if item.user_id == reservation.user_id
            and item.run_id == reservation.run_id
            and item.patch_id == reservation.patch_id
            and item.status is ReservationStatus.ACTIVE
        ]
        for current in own:
            if (
                current.patch_revision == reservation.patch_revision
                and current.rect == reservation.rect
                and current.worksheet_id == reservation.worksheet_id
            ):
                return current
            if current.patch_revision > reservation.patch_revision:
                raise SpatialReservationConflictError(
                    "a newer revision of this patch already holds a reservation",
                    conflicts=(current,),
                )
        conflicts = tuple(
            item
            for item in self._entries.values()
            if item.user_id == reservation.user_id
            and item.workbook_id == reservation.workbook_id
            and item.worksheet_id == reservation.worksheet_id
            and item.patch_id != reservation.patch_id
            and item.is_live(now=now)
            and item.rect.intersects(reservation.rect)
        )
        if conflicts:
            raise SpatialReservationConflictError(
                "the target rectangle overlaps an active reservation",
                conflicts=conflicts,
            )
        for current in own:
            self._entries[current.reservation_id] = current.model_copy(
                update={
                    "status": ReservationStatus.RELEASED,
                    "released_reason": "superseded_by_patch_revision",
                    "updated_at": now,
                    "version": current.version + 1,
                }
            )
        self._entries[reservation.reservation_id] = reservation
        return reservation

    async def list_active(
        self,
        *,
        user_id: str,
        workbook_id: str,
        worksheet_id: str | None = None,
        exclude_run_id: str | None = None,
        now: datetime | None = None,
        limit: int = 500,
    ) -> tuple[SpatialReservation, ...]:
        moment = _as_utc(now or utc_now())
        return tuple(
            item
            for item in self._entries.values()
            if item.user_id == user_id
            and item.workbook_id == workbook_id
            and (worksheet_id is None or item.worksheet_id == worksheet_id)
            and (exclude_run_id is None or item.run_id != exclude_run_id)
            and item.is_live(now=moment)
        )[:limit]

    async def release(
        self,
        *,
        user_id: str,
        reservation_id: str,
        status: ReservationStatus,
        reason: str | None = None,
    ) -> SpatialReservation | None:
        if status is ReservationStatus.ACTIVE:
            raise ValueError("release requires a terminal reservation status")
        current = self._entries.get(reservation_id)
        if (
            current is None
            or current.user_id != user_id
            or current.status is not ReservationStatus.ACTIVE
        ):
            return None
        released = current.model_copy(
            update={
                "status": status,
                "released_reason": reason,
                "updated_at": utc_now(),
                "version": current.version + 1,
            }
        )
        self._entries[reservation_id] = released
        return released

    async def release_for_run(
        self,
        *,
        user_id: str,
        run_id: str,
        status: ReservationStatus,
        reason: str | None = None,
        keep_reservation_id: str | None = None,
    ) -> int:
        released = 0
        for reservation_id, current in list(self._entries.items()):
            if (
                current.user_id != user_id
                or current.run_id != run_id
                or current.status is not ReservationStatus.ACTIVE
                or reservation_id == keep_reservation_id
            ):
                continue
            await self.release(
                user_id=user_id,
                reservation_id=reservation_id,
                status=status,
                reason=reason,
            )
            released += 1
        return released

    async def expire_due(self, *, now: datetime | None = None) -> int:
        moment = _as_utc(now or utc_now())
        expired = 0
        for reservation_id, current in list(self._entries.items()):
            if current.status is not ReservationStatus.ACTIVE:
                continue
            if _as_utc(current.expires_at) > moment:
                continue
            self._entries[reservation_id] = current.model_copy(
                update={
                    "status": ReservationStatus.EXPIRED,
                    "released_reason": "lease_expired",
                    "updated_at": moment,
                    "version": current.version + 1,
                }
            )
            expired += 1
        return expired


__all__ = [
    "InMemorySpatialReservationRepository",
    "MongoSpatialReservationRepository",
    "SpatialReservationConflictError",
    "SpatialReservationError",
    "SpatialReservationRepository",
]
