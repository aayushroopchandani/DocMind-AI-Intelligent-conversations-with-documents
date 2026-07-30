from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from ..models.events import AnalysisRunEvent
from ..models.runs import AnalysisRun, TERMINAL_RUN_STATUSES


SSE_TRANSPORT_EVENT = "analysis_run_event"


class EventReplayStore(Protocol):
    """Minimum durable read surface required by an SSE observer."""

    async def get_run(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> AnalysisRun | None: ...

    async def list_events(
        self,
        *,
        user_id: str,
        run_id: str,
        after_sequence: int,
        limit: int,
    ) -> Sequence[AnalysisRunEvent]: ...


@dataclass(frozen=True, slots=True)
class EventStreamConfig:
    poll_seconds: float = 0.75
    heartbeat_seconds: float = 15.0
    batch_size: int = 100
    retry_milliseconds: int = 2_000

    def __post_init__(self) -> None:
        if self.poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        if self.heartbeat_seconds < self.poll_seconds:
            raise ValueError("heartbeat_seconds cannot be shorter than polling")
        if not 1 <= self.batch_size <= 500:
            raise ValueError("batch_size must be between 1 and 500")
        if not 100 <= self.retry_milliseconds <= 60_000:
            raise ValueError("retry_milliseconds must be between 100 and 60000")


def encode_event(event: AnalysisRunEvent) -> bytes:
    """Serialize one persisted event using a stable SSE transport event name."""

    data = event.model_dump(mode="json")
    data["event_version"] = data.pop("schema_version")
    encoded = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (
        f"id: {event.sequence}\n"
        f"event: {SSE_TRANSPORT_EVENT}\n"
        f"data: {encoded}\n\n"
    ).encode("utf-8")


def heartbeat_frame() -> bytes:
    """Heartbeats are transport comments and never consume event sequences."""

    return b": heartbeat\n\n"


async def replayable_event_stream(
    *,
    store: EventReplayStore,
    user_id: str,
    run_id: str,
    after_sequence: int = 0,
    disconnected: Callable[[], Awaitable[bool]] | None = None,
    config: EventStreamConfig | None = None,
) -> AsyncIterator[bytes]:
    """Replay durable events, then poll until the terminal event is observed.

    Pipeline execution is intentionally independent from this generator.
    Closing a browser only stops observation; it never cancels the run.
    """

    if after_sequence < 0:
        raise ValueError("after_sequence must be non-negative")
    active = config or EventStreamConfig()
    cursor = after_sequence
    elapsed_since_heartbeat = 0.0

    yield f"retry: {active.retry_milliseconds}\n\n".encode("ascii")

    while True:
        if disconnected is not None and await disconnected():
            return

        events = await store.list_events(
            user_id=user_id,
            run_id=run_id,
            after_sequence=cursor,
            limit=active.batch_size,
        )
        if events:
            for event in events:
                # Defend against a buggy repository without duplicating frames.
                if event.sequence <= cursor:
                    continue
                yield encode_event(event)
                cursor = event.sequence
                if event.status in TERMINAL_RUN_STATUSES:
                    # Terminal state and its event commit in one Mongo
                    # transaction, so observing this frame is a sufficient and
                    # race-free close condition.
                    return
            elapsed_since_heartbeat = 0.0
            # Drain a full backlog without a polling delay.
            if len(events) >= active.batch_size:
                continue

        await asyncio.sleep(active.poll_seconds)
        elapsed_since_heartbeat += active.poll_seconds
        if elapsed_since_heartbeat >= active.heartbeat_seconds:
            # Recheck the parent only at heartbeat cadence. Every lifecycle
            # update has a durable event, so reading the run on every subsecond
            # poll doubles idle Mongo traffic without improving replay.
            run = await store.get_run(user_id=user_id, run_id=run_id)
            if run is None:
                # Ownership/not-found is checked before response creation. If
                # the record disappears later, end without a synthetic event.
                return
            if (
                run.status in TERMINAL_RUN_STATUSES
                and cursor >= run.last_event_sequence
            ):
                return
            yield heartbeat_frame()
            elapsed_since_heartbeat = 0.0


__all__ = [
    "EventReplayStore",
    "EventStreamConfig",
    "SSE_TRANSPORT_EVENT",
    "encode_event",
    "heartbeat_frame",
    "replayable_event_stream",
]
