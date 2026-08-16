"""Bounded child-process backend (Phase 9.4.4).

Why a process and not a thread: a thread cannot be killed. A wall-clock timeout,
a runaway join or a cancelled run all need the work to actually stop, and only a
separate process gives that. It also makes credential isolation structural — the
child starts from a scrubbed environment, so there is no connection string or
API key in scope for it to reach.

Polars is trusted application code, so this is isolation, not a sandbox for
untrusted input. Arbitrary Python remains out of scope for Phase 9.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..contracts import (
    ExecutionFailureCode,
    NativeExecutionResult,
    NativeRecipe,
)
from . import semantics
from .engine import engine_version


_WORKER_MODULE = "scripts.data_analysis_agent.runtime.execution.native.worker_main"

CANCELLATION_POLL_SECONDS = 0.5
"""How often the parent asks whether the run was cancelled while the child runs."""


class _Timeout(Exception):
    """The child exceeded its wall-clock budget."""


class _Cancelled(Exception):
    """The run was cancelled while the child was running."""


def _memory_limiter(megabytes: int) -> Callable[[], None] | None:
    """Return a pre-exec hook capping the child's address space.

    POSIX only, and even there enforcement varies: Linux honours `RLIMIT_AS`
    reliably, macOS often does not. The hook is best-effort — the wall-clock
    timeout and the kill remain the guarantees that always hold.
    """

    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX host
        return None

    limit = megabytes * 1024 * 1024

    def apply() -> None:  # pragma: no cover - runs in the forked child
        for name in ("RLIMIT_AS", "RLIMIT_DATA"):
            constant: Any = getattr(resource, name, None)
            if constant is None:
                continue
            try:
                soft, hard = resource.getrlimit(constant)
                ceiling = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
                resource.setrlimit(constant, (ceiling, hard))
            except (ValueError, OSError):
                continue

    return apply

# Everything the interpreter genuinely needs to start. Anything carrying a
# secret — MONGODB_URI, CLOUDINARY_*, OPENAI_API_KEY, QDRANT_* — is absent by
# construction because this is an allowlist, not a denylist.
_ENV_ALLOWLIST = (
    "PATH",
    "PYTHONPATH",
    "PYTHONHASHSEED",
    "PYTHONIOENCODING",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "SYSTEMROOT",
)


def scrubbed_environment(*, project_root: Path) -> dict[str, str]:
    """Return the minimal environment handed to the child process."""

    environment = {
        name: os.environ[name] for name in _ENV_ALLOWLIST if name in os.environ
    }
    existing = environment.get("PYTHONPATH", "")
    root = str(project_root)
    environment["PYTHONPATH"] = (
        root if not existing else os.pathsep.join((root, existing))
    )
    # Deterministic hashing keeps any incidental set/dict ordering stable
    # between the parent and a replay.
    environment["PYTHONHASHSEED"] = "0"
    return environment


class SubprocessNativeBackend:
    """Runs the engine in a killable child process with a scrubbed environment."""

    isolation = "bounded_subprocess"

    def __init__(
        self,
        *,
        project_root: Path | None = None,
        python_executable: str | None = None,
    ) -> None:
        # backend.py -> native -> execution -> runtime -> data_analysis_agent
        # -> scripts -> backend
        self._project_root = project_root or Path(__file__).resolve().parents[5]
        self._python = python_executable or sys.executable

    async def execute(
        self,
        recipe: NativeRecipe,
        *,
        output_path: Path,
        cancelled: Callable[[], Awaitable[bool]] | None = None,
    ) -> NativeExecutionResult:
        staging = output_path.parent
        job_path = staging / "job.json"
        manifest_path = staging / "result.json"
        job_path.write_text(recipe.model_dump_json(), encoding="utf-8")

        process = await asyncio.create_subprocess_exec(
            self._python,
            "-m",
            _WORKER_MODULE,
            str(job_path),
            str(manifest_path),
            cwd=str(self._project_root),
            env=scrubbed_environment(project_root=self._project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_memory_limiter(recipe.limits.max_memory_mb),
        )
        try:
            stderr = await self._await_child(process, recipe, cancelled)
        except _Timeout:
            await self._terminate(process)
            return self._failure(
                ExecutionFailureCode.TIMEOUT,
                "native execution exceeded "
                f"{recipe.limits.wall_clock_seconds:.1f}s and was terminated",
            )
        except _Cancelled:
            await self._terminate(process)
            return self._failure(
                ExecutionFailureCode.CANCELLED,
                "the run was cancelled and the native worker was terminated",
            )
        except asyncio.CancelledError:
            await self._terminate(process)
            raise

        if not manifest_path.exists():
            detail = stderr.decode("utf-8", "replace").strip().splitlines()
            return self._failure(
                ExecutionFailureCode.ENGINE_CRASHED,
                "native worker exited without a manifest "
                f"(code {process.returncode}): {detail[-1] if detail else 'no output'}",
            )
        return NativeExecutionResult.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )

    async def _await_child(
        self,
        process: asyncio.subprocess.Process,
        recipe: NativeRecipe,
        cancelled: Callable[[], Awaitable[bool]] | None,
    ) -> bytes:
        """Wait for the child, honouring both the deadline and cancellation.

        Cancellation is polled rather than pushed because the authoritative flag
        lives in MongoDB. `CANCELLATION_POLL_SECONDS` bounds how long a
        cancelled run keeps burning CPU.
        """

        communicate = asyncio.ensure_future(process.communicate())
        deadline = asyncio.get_running_loop().time() + recipe.limits.wall_clock_seconds
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise _Timeout
                slice_seconds = (
                    min(CANCELLATION_POLL_SECONDS, remaining)
                    if cancelled is not None
                    else remaining
                )
                done, _pending = await asyncio.wait(
                    (communicate,),
                    timeout=slice_seconds,
                )
                if done:
                    _stdout, stderr = communicate.result()
                    return stderr
                if cancelled is not None and await cancelled():
                    raise _Cancelled
        finally:
            if not communicate.done():
                communicate.cancel()

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:  # pragma: no cover - OS refused the kill
            pass

    @staticmethod
    def _failure(
        code: ExecutionFailureCode,
        message: str,
    ) -> NativeExecutionResult:
        return NativeExecutionResult(
            succeeded=False,
            engine_version=engine_version(),
            semantics_version=semantics.NATIVE_SEMANTICS_VERSION,
            failure_code=code,
            failure_message=message[:1_000],
        )


__all__ = ["SubprocessNativeBackend", "scrubbed_environment"]
