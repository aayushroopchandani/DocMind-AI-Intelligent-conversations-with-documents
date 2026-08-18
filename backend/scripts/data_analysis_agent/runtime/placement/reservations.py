"""Holding a rectangle while a patch waits for approval (Phase 9.11.5).

A patch is compiled, reviewed and applied over minutes, not milliseconds. For
that whole window the rectangle it targets has to stay claimed, or a second run
could place its own output on top of the first while the user is still reading
the preview.

The claim is a lease. Its expiry is generous enough to survive a slow review and
short enough that a crashed worker does not park a rectangle forever, and every
terminal outcome — rejection, cancellation, application, supersession — releases
it immediately rather than waiting for that expiry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from uuid import uuid4

from ..models.reservations import (
    ReservationStatus,
    SpatialReservation,
    utc_now,
)
from ..models.workbook import Rect
from ..repositories.reservations import (
    SpatialReservationConflictError,
    SpatialReservationRepository,
)
from .selection import PlacementDecision


DEFAULT_RESERVATION_SECONDS = 1_800
"""Half an hour: long enough to review a patch, short enough to recover from."""


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    user_id: str
    workspace_id: str
    workbook_id: str
    run_id: str
    patch_id: str
    patch_revision: int
    base_revision: int
    lease_owner: str


class WriteReservationService:
    """Reserves and releases the rectangles patches are about to write."""

    def __init__(
        self,
        repository: SpatialReservationRepository,
        *,
        lease_seconds: int = DEFAULT_RESERVATION_SECONDS,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if lease_seconds < 60:
            raise ValueError("reservation leases must last at least a minute")
        self._repository = repository
        self._lease_seconds = lease_seconds
        self._clock = clock

    async def occupied_rectangles(
        self,
        *,
        user_id: str,
        workbook_id: str,
        exclude_run_id: str | None = None,
    ) -> dict[str, tuple[tuple[Rect, str], ...]]:
        """Return live reservations held by other runs, keyed by worksheet.

        Shaped for `occupancy.inspect_rectangle`, which needs the rectangle and
        a label to explain the collision with.
        """

        active = await self._repository.list_active(
            user_id=user_id,
            workbook_id=workbook_id,
            exclude_run_id=exclude_run_id,
            now=self._clock(),
        )
        grouped: dict[str, list[tuple[Rect, str]]] = {}
        for reservation in active:
            grouped.setdefault(reservation.worksheet_id, []).append(
                (reservation.rect, reservation.owner_label())
            )
        return {key: tuple(value) for key, value in grouped.items()}

    async def reserve(
        self,
        decision: PlacementDecision,
        *,
        request: ReservationRequest,
    ) -> SpatialReservation:
        """Claim the decided rectangle, releasing this run's earlier claims."""

        now = self._clock()
        reservation = SpatialReservation.for_rect(
            decision.target_rect,
            reservation_id=str(uuid4()),
            user_id=request.user_id,
            workspace_id=request.workspace_id,
            workbook_id=request.workbook_id,
            worksheet_id=decision.worksheet_id,
            run_id=request.run_id,
            patch_id=request.patch_id,
            patch_revision=request.patch_revision,
            base_revision=request.base_revision,
            lease_owner=request.lease_owner,
            expires_at=now + timedelta(seconds=self._lease_seconds),
        ).model_copy(update={"created_at": now, "updated_at": now})
        claimed = await self._repository.reserve(reservation)
        # A run holds exactly one rectangle at a time. Anything it claimed for a
        # superseded patch is released here, not left to expire.
        await self._repository.release_for_run(
            user_id=request.user_id,
            run_id=request.run_id,
            status=ReservationStatus.RELEASED,
            reason="superseded_by_new_patch",
            keep_reservation_id=claimed.reservation_id,
        )
        return claimed

    async def release(
        self,
        *,
        user_id: str,
        reservation_id: str,
        status: ReservationStatus = ReservationStatus.RELEASED,
        reason: str | None = None,
    ) -> SpatialReservation | None:
        return await self._repository.release(
            user_id=user_id,
            reservation_id=reservation_id,
            status=status,
            reason=reason,
        )

    async def release_run(
        self,
        *,
        user_id: str,
        run_id: str,
        status: ReservationStatus = ReservationStatus.RELEASED,
        reason: str | None = None,
    ) -> int:
        return await self._repository.release_for_run(
            user_id=user_id,
            run_id=run_id,
            status=status,
            reason=reason,
        )

    async def sweep_expired(self) -> int:
        return await self._repository.expire_due(now=self._clock())


__all__ = [
    "DEFAULT_RESERVATION_SECONDS",
    "ReservationRequest",
    "SpatialReservationConflictError",
    "WriteReservationService",
]
