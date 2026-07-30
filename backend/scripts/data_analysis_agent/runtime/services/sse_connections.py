from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4


class SSEConnectionLimitError(RuntimeError):
    """The process has no safe capacity for another durable event stream."""


@dataclass(frozen=True, slots=True)
class SSEConnectionLimits:
    total: int = 200
    per_user: int = 8
    per_run: int = 2

    def __post_init__(self) -> None:
        if min(self.total, self.per_user, self.per_run) <= 0:
            raise ValueError("SSE connection limits must be positive")
        if not self.per_run <= self.per_user <= self.total:
            raise ValueError(
                "SSE limits must satisfy per_run <= per_user <= total"
            )


@dataclass(frozen=True, slots=True)
class SSEConnectionLease:
    token: str
    user_id: str
    run_id: str


class SSEConnectionLimiter:
    """Process-local admission control for long-lived Mongo polling streams."""

    def __init__(self, limits: SSEConnectionLimits | None = None) -> None:
        self._limits = limits or SSEConnectionLimits()
        self._lock = asyncio.Lock()
        self._leases: dict[str, SSEConnectionLease] = {}
        self._by_user: dict[str, int] = {}
        self._by_run: dict[tuple[str, str], int] = {}

    async def acquire(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> SSEConnectionLease:
        run_key = (user_id, run_id)
        async with self._lock:
            if len(self._leases) >= self._limits.total:
                raise SSEConnectionLimitError(
                    "event-stream capacity is temporarily exhausted"
                )
            if self._by_user.get(user_id, 0) >= self._limits.per_user:
                raise SSEConnectionLimitError(
                    "the user has too many open event streams"
                )
            if self._by_run.get(run_key, 0) >= self._limits.per_run:
                raise SSEConnectionLimitError(
                    "the run has too many open event streams"
                )
            lease = SSEConnectionLease(
                token=str(uuid4()),
                user_id=user_id,
                run_id=run_id,
            )
            self._leases[lease.token] = lease
            self._by_user[user_id] = self._by_user.get(user_id, 0) + 1
            self._by_run[run_key] = self._by_run.get(run_key, 0) + 1
            return lease

    async def release(self, lease: SSEConnectionLease) -> None:
        async with self._lock:
            active = self._leases.pop(lease.token, None)
            if active is None:
                return
            user_count = self._by_user[active.user_id] - 1
            if user_count:
                self._by_user[active.user_id] = user_count
            else:
                self._by_user.pop(active.user_id, None)
            run_key = (active.user_id, active.run_id)
            run_count = self._by_run[run_key] - 1
            if run_count:
                self._by_run[run_key] = run_count
            else:
                self._by_run.pop(run_key, None)

    @property
    def active_count(self) -> int:
        return len(self._leases)


__all__ = [
    "SSEConnectionLease",
    "SSEConnectionLimitError",
    "SSEConnectionLimiter",
    "SSEConnectionLimits",
]
