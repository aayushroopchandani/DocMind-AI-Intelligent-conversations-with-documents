from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .artifacts import (
    ArtifactReconciliationSummary,
    ArtifactVersionService,
)
from ..observability.logging import get_analysis_logger


logger = get_analysis_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ArtifactReconcilerConfig:
    interval_seconds: float = 30.0
    stale_after_seconds: float = 120.0
    batch_size: int = 25

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if self.stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        if not 1 <= self.batch_size <= 500:
            raise ValueError("batch_size must be between 1 and 500")


class ArtifactUploadReconciler:
    """Bounded periodic recovery for interrupted two-stage artifact uploads."""

    def __init__(
        self,
        *,
        service: ArtifactVersionService,
        config: ArtifactReconcilerConfig | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._service = service
        self._config = config or ArtifactReconcilerConfig()
        self._clock = clock
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(),
            name="artifact-upload-reconciler",
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def reconcile_once(self) -> ArtifactReconciliationSummary:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("artifact reconciler clock must be timezone-aware")
        return await self._service.reconcile_stale_versions(
            stale_before=now.astimezone(timezone.utc)
            - timedelta(seconds=self._config.stale_after_seconds),
            limit=self._config.batch_size,
        )

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    summary = await self.reconcile_once()
                    if summary.inspected:
                        logger.info(
                            "artifact reconciliation sweep completed",
                            extra={
                                "inspected": summary.inspected,
                                "finalized": summary.finalized,
                                "pointer_repaired": summary.pointer_repaired,
                                "failed": summary.failed,
                                "pending": summary.pending,
                            },
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A provider or repository outage must not terminate future
                    # sweeps; stale reservations remain durable for the retry.
                    logger.exception("artifact reconciliation sweep failed")
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self._config.interval_seconds,
                    )
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise


__all__ = [
    "ArtifactReconcilerConfig",
    "ArtifactUploadReconciler",
]
