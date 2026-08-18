"""Exact rectangle write reservations (Phase 9.11.5).

Phase 8 reserved a whole worksheet while a plan was live. That is correct but
blunt: two runs writing to opposite corners of the same sheet cannot collide,
and serializing them costs the user nothing but waiting. Phase 9 reserves the
rectangle instead.

MongoDB cannot express "no two active rectangles may overlap" as an index —
there is no such constraint — so the invariant is enforced by the repository:
query intersecting active rectangles and insert inside one transaction. The
unique index that *does* exist covers the other half of the problem, which is
duplicate reservation of the same patch revision.

A reservation is a lease, not a lock. It carries an owner and an expiry, so a
worker that dies holding one does not block the rectangle forever.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workbook import MAX_WORKBOOK_COLUMNS, MAX_WORKBOOK_ROWS, Rect


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReservationStatus(str, Enum):
    ACTIVE = "active"
    RELEASED = "released"
    APPLIED = "applied"
    EXPIRED = "expired"


TERMINAL_RESERVATION_STATUSES = frozenset(
    {
        ReservationStatus.RELEASED,
        ReservationStatus.APPLIED,
        ReservationStatus.EXPIRED,
    }
)


class SpatialReservation(BaseModel):
    """One run's exclusive claim on one rectangle of one worksheet."""

    schema_version: int = 1
    reservation_id: str = Field(min_length=1, max_length=120)
    user_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)

    workbook_id: str = Field(min_length=1, max_length=200)
    worksheet_id: str = Field(min_length=1, max_length=200)
    first_row: int = Field(ge=1, le=MAX_WORKBOOK_ROWS)
    last_row: int = Field(ge=1, le=MAX_WORKBOOK_ROWS)
    first_column: int = Field(ge=1, le=MAX_WORKBOOK_COLUMNS)
    last_column: int = Field(ge=1, le=MAX_WORKBOOK_COLUMNS)

    run_id: str = Field(min_length=36, max_length=36)
    patch_id: str = Field(min_length=1, max_length=120)
    patch_revision: int = Field(ge=1)
    base_revision: int = Field(ge=0)

    status: ReservationStatus = ReservationStatus.ACTIVE
    lease_owner: str = Field(min_length=1, max_length=200)
    expires_at: datetime
    released_reason: str | None = Field(default=None, max_length=200)

    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_rectangle(self) -> Self:
        if self.last_row < self.first_row or self.last_column < self.first_column:
            raise ValueError("reservation end cannot precede its start")
        return self

    @classmethod
    def for_rect(
        cls,
        rect: Rect,
        *,
        reservation_id: str,
        user_id: str,
        workspace_id: str,
        workbook_id: str,
        worksheet_id: str,
        run_id: str,
        patch_id: str,
        patch_revision: int,
        base_revision: int,
        lease_owner: str,
        expires_at: datetime,
    ) -> SpatialReservation:
        return cls(
            reservation_id=reservation_id,
            user_id=user_id,
            workspace_id=workspace_id,
            workbook_id=workbook_id,
            worksheet_id=worksheet_id,
            first_row=rect.first_row,
            last_row=rect.last_row,
            first_column=rect.first_column,
            last_column=rect.last_column,
            run_id=run_id,
            patch_id=patch_id,
            patch_revision=patch_revision,
            base_revision=base_revision,
            lease_owner=lease_owner,
            expires_at=expires_at,
        )

    @property
    def rect(self) -> Rect:
        return Rect(
            first_row=self.first_row,
            first_column=self.first_column,
            last_row=self.last_row,
            last_column=self.last_column,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RESERVATION_STATUSES

    def is_live(self, *, now: datetime | None = None) -> bool:
        moment = now or utc_now()
        expiry = (
            self.expires_at
            if self.expires_at.tzinfo is not None
            else self.expires_at.replace(tzinfo=timezone.utc)
        )
        return self.status is ReservationStatus.ACTIVE and expiry > moment

    def owner_label(self) -> str:
        return f"run {self.run_id}"


__all__ = [
    "TERMINAL_RESERVATION_STATUSES",
    "ReservationStatus",
    "SpatialReservation",
    "utc_now",
]
