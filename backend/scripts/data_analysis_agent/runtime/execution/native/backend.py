"""The execution backend seam.

Keeping the engine behind a protocol is what lets the same compiled recipe run
in-process (fast, used by tests and by the compiler suite) or inside a bounded
child process (used in production), and lets a future deployment swap in a queue
or container worker without touching the compilers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from ..contracts import NativeExecutionResult, NativeRecipe
from .engine import execute_recipe


CancellationCheck = Callable[[], Awaitable[bool]]


class NativeExecutionBackend(Protocol):
    """Runs one validated recipe and returns a verified manifest."""

    @property
    def isolation(self) -> str:
        """Human-readable isolation level, recorded on every execution."""

    async def execute(
        self,
        recipe: NativeRecipe,
        *,
        output_path: Path,
        cancelled: CancellationCheck | None = None,
    ) -> NativeExecutionResult: ...


class InProcessNativeBackend:
    """Runs the engine on a worker thread in this process.

    The event loop stays free, but the execution shares this process's memory
    and environment, and a thread cannot be killed — so a cancellation request
    is honoured only at the boundaries, not mid-stage. Use this for tests and
    local development; production should prefer the subprocess backend.
    """

    isolation = "in_process_thread"

    async def execute(
        self,
        recipe: NativeRecipe,
        *,
        output_path: Path,
        cancelled: CancellationCheck | None = None,
    ) -> NativeExecutionResult:
        return await asyncio.to_thread(
            execute_recipe,
            recipe,
            output_path=output_path,
        )


__all__ = ["InProcessNativeBackend", "NativeExecutionBackend"]
