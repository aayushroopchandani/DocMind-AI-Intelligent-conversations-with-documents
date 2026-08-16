"""Orchestration entry point for one native execution.

The sequence is deliberately ordered so that the cheap, conclusive checks happen
before any resource is allocated (Phase 9.3.2 — "before allocating execution
resources"):

    preconditions -> cache lookup -> compile -> resolve inputs -> stage -> run

Staging is the first step that costs real memory or disk, and it only happens
after the plan has been proven current and every input has been re-verified
against what the plan was built on.

The service owns the staging directory lifetime: everything a run writes lives
under one private temporary directory that is removed when the run finishes,
whether it succeeded or not.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from ..models.capabilities import ExecutorCapabilities
from ..models.plans import AnalysisPlan, PlanColumn
from .admission import (
    AdmissionDecision,
    ExecutionAdmission,
    RunAdmissionState,
    check_execution_preconditions,
)
from .contracts import (
    ExecutionFailureCode,
    ExecutionLimits,
    NativeExecutionResult,
    NativeInputTable,
    NativeRecipe,
    StepMetrics,
)
from .dag import RecipeCompilationError, compile_recipe
from .idempotency import execution_key
from .inputs import InputResolutionError, NormalizedInputResolver
from .native import staging
from .native.backend import (
    CancellationCheck,
    InProcessNativeBackend,
    NativeExecutionBackend,
)
from .native.engine import engine_version
from .native.semantics import NATIVE_SEMANTICS_VERSION


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """What one execution attempt produced, ready for the run lifecycle."""

    succeeded: bool
    execution_key: str
    engine_version: str
    semantics_version: str
    isolation: str
    cache_hit: bool = False
    result_columns: tuple[PlanColumn, ...] = ()
    row_count: int = 0
    content_hash: str | None = None
    output_bytes: int = 0
    step_metrics: tuple[StepMetrics, ...] = ()
    duration_ms: float = 0.0
    failure_code: ExecutionFailureCode | None = None
    failure_message: str | None = None
    admission: AdmissionDecision | None = None

    @property
    def rejected_by_admission(self) -> bool:
        return self.admission is not None and self.admission.rejected


class ExecutionResultStore:
    """Tenant-scoped record of verified executions, keyed by execution key.

    A real deployment persists this in MongoDB alongside the published artifact.
    The in-memory default keeps the orchestration testable and lets a single
    process satisfy "duplicate queue delivery produces one logical execution".
    """

    def __init__(self) -> None:
        self._entries: dict[str, ExecutionOutcome] = {}

    async def get(self, key: str) -> ExecutionOutcome | None:
        return self._entries.get(key)

    async def put(self, key: str, outcome: ExecutionOutcome) -> None:
        if outcome.succeeded:
            self._entries[key] = outcome


class NativeExecutionService:
    """Runs one validated plan end to end and returns a typed outcome."""

    def __init__(
        self,
        *,
        resolver: NormalizedInputResolver,
        backend: NativeExecutionBackend | None = None,
        result_store: ExecutionResultStore | None = None,
        capabilities: ExecutorCapabilities | None = None,
        limits: ExecutionLimits | None = None,
        staging_root: Path | None = None,
    ) -> None:
        self._resolver = resolver
        self._backend = backend or InProcessNativeBackend()
        self._results = result_store or ExecutionResultStore()
        self._capabilities = capabilities or ExecutorCapabilities()
        self._limits = limits or ExecutionLimits()
        self._staging_root = staging_root

    async def execute(
        self,
        *,
        plan: AnalysisPlan,
        run: RunAdmissionState,
        cancelled: CancellationCheck | None = None,
    ) -> ExecutionOutcome:
        decision = check_execution_preconditions(
            plan,
            run,
            capabilities=self._capabilities,
        )
        if decision.admission is not ExecutionAdmission.QUEUE:
            return self._admission_outcome(plan, decision)

        try:
            recipe_plan = compile_recipe(plan)
        except RecipeCompilationError as error:
            return self._failure(plan, error.code, error.message)

        key = execution_key(plan, result_alias=recipe_plan.result_alias)
        cached = await self._results.get(key)
        if cached is not None:
            # Identical tenant, inputs, recipe, engine and semantics: replaying
            # would produce the same content hash by construction.
            return replace(cached, cache_hit=True)

        try:
            resolved = await self._resolver.resolve(
                user_id=plan.user_id,
                workspace_id=plan.workspace_id,
                datasets=plan.input_datasets,
            )
        except InputResolutionError as error:
            return self._failure(plan, error.code, error.message, key=key)

        directory = Path(
            tempfile.mkdtemp(
                prefix=f"native-{plan.run_id}-",
                dir=str(self._staging_root) if self._staging_root else None,
            )
        )
        try:
            inputs: list[NativeInputTable] = []
            for item in resolved:
                path = directory / f"input-{item.alias}.arrow"
                try:
                    staging.write_ipc(item.columns, item.rows, path=path)
                except staging.StagingError as error:
                    return self._failure(
                        plan,
                        ExecutionFailureCode.SCHEMA_MISMATCH,
                        str(error),
                        key=key,
                    )
                inputs.append(
                    NativeInputTable(
                        alias=item.alias,
                        dataset_id=item.dataset_id,
                        content_signature=item.content_signature,
                        columns=item.columns,
                        row_count=item.row_count,
                        ipc_path=str(path),
                    )
                )
            recipe = NativeRecipe(
                engine_version=engine_version(),
                semantics_version=NATIVE_SEMANTICS_VERSION,
                steps=recipe_plan.steps,
                inputs=tuple(inputs),
                result_alias=recipe_plan.result_alias,
                limits=self._limits,
            )
            result = await self._backend.execute(
                recipe,
                output_path=directory / "result.arrow",
                cancelled=cancelled,
            )
            outcome = self._outcome(key, result)
            await self._results.put(key, outcome)
            return outcome
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def _outcome(
        self,
        key: str,
        result: NativeExecutionResult,
    ) -> ExecutionOutcome:
        return ExecutionOutcome(
            succeeded=result.succeeded,
            execution_key=key,
            engine_version=result.engine_version,
            semantics_version=result.semantics_version,
            isolation=self._backend.isolation,
            result_columns=result.result_columns,
            row_count=result.row_count,
            content_hash=result.content_hash,
            output_bytes=result.output_bytes,
            step_metrics=result.step_metrics,
            duration_ms=result.duration_ms,
            failure_code=result.failure_code,
            failure_message=result.failure_message,
        )

    def _failure(
        self,
        plan: AnalysisPlan,
        code: ExecutionFailureCode,
        message: str,
        *,
        key: str | None = None,
    ) -> ExecutionOutcome:
        return ExecutionOutcome(
            succeeded=False,
            execution_key=key or "",
            engine_version=engine_version(),
            semantics_version=NATIVE_SEMANTICS_VERSION,
            isolation=self._backend.isolation,
            failure_code=code,
            failure_message=message,
        )

    def _admission_outcome(
        self,
        plan: AnalysisPlan,
        decision: AdmissionDecision,
    ) -> ExecutionOutcome:
        return ExecutionOutcome(
            succeeded=False,
            execution_key="",
            engine_version=engine_version(),
            semantics_version=NATIVE_SEMANTICS_VERSION,
            isolation=self._backend.isolation,
            failure_code=(
                ExecutionFailureCode.CANCELLED
                if decision.code == "execution_cancelled"
                else ExecutionFailureCode.INPUT_UNAVAILABLE
            ),
            failure_message=decision.message,
            admission=decision,
        )



__all__ = [
    "ExecutionOutcome",
    "ExecutionResultStore",
    "NativeExecutionService",
]
