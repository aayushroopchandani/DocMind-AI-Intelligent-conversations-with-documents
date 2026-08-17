"""Validate, store and commit one execution result (Phase 9.8.5 + 9.9).

The ordering here is the whole point, so it is stated once and followed exactly:

    validate -> upload bundle -> compare-and-set commit

Validating first means nothing invalid is ever stored. Uploading before
committing means a crash leaves objects without a record — recoverable — rather
than a record without objects, which nothing can repair. Committing through a
fenced compare-and-set means a worker that lost its lease can finish computing
but cannot publish over the attempt that replaced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import polars as pl

from ..models.executions import (
    AnalysisExecution,
    ExecutionMetrics,
    ExecutionStatus,
    ResultArtifacts,
)
from ..models.plans import AnalysisPlan, PlanAssertion
from ..models.privacy import AnalysisPrivacyMode
from ..repositories.executions import (
    ExecutionFencedError,
    ExecutionRepository,
)
from ..storage.base import ArtifactBlobStore
from .contracts import (
    ExecutionFailureCode,
    ExecutionLimits,
    NativeExecutionResult,
    NativeRecipe,
)
from .dag import CompiledRecipe
from .idempotency import dataset_content_signature, native_recipe_hash
from .native.engine import result_content_hash as recompute_content_hash
from .results import (
    ResultPublicationError,
    build_lineage,
    build_preview,
    publish_result,
    validate_result,
)


@dataclass(frozen=True, slots=True)
class PublicationOutcome:
    """What finalization decided about a finished execution."""

    execution: AnalysisExecution
    published: bool
    failure_code: ExecutionFailureCode | None = None
    failure_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.published and self.failure_code is None


class ResultPublisher:
    """Owns the durable half of an execution: its record and its artifacts."""

    def __init__(
        self,
        *,
        repository: ExecutionRepository,
        store: ArtifactBlobStore,
        privacy_mode: AnalysisPrivacyMode = AnalysisPrivacyMode.STANDARD,
    ) -> None:
        self._repository = repository
        self._store = store
        self._privacy_mode = privacy_mode

    async def reserve(
        self,
        *,
        plan: AnalysisPlan,
        recipe_plan: CompiledRecipe,
        execution_key: str,
        engine_version: str,
        semantics_version: str,
    ) -> AnalysisExecution:
        """Claim the execution key before any work or upload begins."""

        return await self._repository.reserve(
            AnalysisExecution(
                execution_id=str(uuid4()),
                execution_key=execution_key,
                user_id=plan.user_id,
                workspace_id=plan.workspace_id,
                run_id=plan.run_id,
                plan_id=plan.plan_id,
                plan_hash=plan.plan_hash,
                input_signatures=tuple(
                    dataset_content_signature(dataset)
                    for dataset in sorted(
                        plan.input_datasets,
                        key=lambda item: item.alias,
                    )
                ),
                recipe_hash=native_recipe_hash(
                    recipe_plan.steps,
                    recipe_plan.result_alias,
                ),
                engine_version=engine_version,
                semantics_version=semantics_version,
            )
        )

    async def start(
        self,
        *,
        execution: AnalysisExecution,
        worker_id: str,
        fencing_token: int,
    ) -> AnalysisExecution:
        return await self._repository.start(
            execution=execution,
            worker_id=worker_id,
            fencing_token=fencing_token,
        )

    async def finalize(
        self,
        *,
        execution: AnalysisExecution,
        plan: AnalysisPlan,
        recipe: NativeRecipe,
        result: NativeExecutionResult,
        output_path: Path,
        limits: ExecutionLimits,
        workbook_bound: bool = False,
    ) -> PublicationOutcome:
        """Validate, store and commit a finished execution."""

        if not result.succeeded:
            return await self._fail(
                execution,
                result.failure_code or ExecutionFailureCode.ENGINE_CRASHED,
                result.failure_message or "native execution failed",
            )

        frame = pl.read_ipc(output_path)
        columns = result.result_columns
        # Recomputed from the bytes that actually arrived, never trusted from
        # the worker's manifest.
        content_hash = recompute_content_hash(frame, columns)

        issues = validate_result(
            result=result,
            frame=frame,
            declared_columns=columns,
            assertions=_result_assertions(plan, recipe),
            limits=limits,
            recomputed_hash=content_hash,
            workbook_bound=workbook_bound,
        )
        if issues:
            first = issues[0]
            return await self._fail(execution, first.code, first.message)

        try:
            bundle = await publish_result(
                store=self._store,
                workspace_id=plan.workspace_id,
                execution_key=execution.execution_key,
                frame=frame,
                columns=columns,
                content_hash=content_hash,
                lineage=build_lineage(
                    plan=plan,
                    recipe=recipe,
                    execution_key=execution.execution_key,
                    recipe_hash=execution.recipe_hash,
                    content_hash=content_hash,
                    result_columns=columns,
                    step_metrics=result.step_metrics,
                ),
                preview=build_preview(
                    frame,
                    columns,
                    privacy_mode=self._privacy_mode,
                ),
            )
        except ResultPublicationError as error:
            return await self._fail(execution, error.code, error.message)

        return await self._commit(
            execution=execution,
            content_hash=content_hash,
            columns=columns,
            artifacts=bundle.artifacts,
            metrics=_metrics(result, frame, bundle.total_bytes),
        )

    async def _commit(
        self,
        *,
        execution: AnalysisExecution,
        content_hash: str,
        columns,
        artifacts: ResultArtifacts,
        metrics: ExecutionMetrics,
    ) -> PublicationOutcome:
        try:
            committed = await self._repository.publish(
                execution=execution,
                content_hash=content_hash,
                columns=columns,
                artifacts=artifacts,
                metrics=metrics,
            )
        except ExecutionFencedError as error:
            # The computation was valid and its objects are stored; a newer
            # attempt simply owns the record now. Reconciliation collects the
            # orphaned bundle.
            return PublicationOutcome(
                execution=execution,
                published=False,
                failure_code=ExecutionFailureCode.CANCELLED,
                failure_message=str(error),
            )
        return PublicationOutcome(execution=committed, published=True)

    async def _fail(
        self,
        execution: AnalysisExecution,
        code: ExecutionFailureCode,
        message: str,
    ) -> PublicationOutcome:
        try:
            failed = await self._repository.fail(
                execution=execution,
                code=code.value,
                message=message,
                cancelled=code is ExecutionFailureCode.CANCELLED,
            )
        except ExecutionFencedError:
            failed = execution
        return PublicationOutcome(
            execution=failed,
            published=False,
            failure_code=code,
            failure_message=message,
        )


def cached_outcome(execution: AnalysisExecution) -> bool:
    """Return whether a reserved record already holds a usable result."""

    return (
        execution.status is ExecutionStatus.SUCCEEDED
        and execution.artifacts is not None
        and execution.result_content_hash is not None
    )


def _result_assertions(
    plan: AnalysisPlan,
    recipe: NativeRecipe,
) -> tuple[PlanAssertion, ...]:
    """Return the assertions attached to the step producing the final result."""

    for step in recipe.steps:
        if step.output_alias == recipe.result_alias:
            return step.assertions
    return ()


def _metrics(
    result: NativeExecutionResult,
    frame: pl.DataFrame,
    output_bytes: int,
) -> ExecutionMetrics:
    first = result.step_metrics[0] if result.step_metrics else None
    return ExecutionMetrics(
        input_rows=first.input_rows if first else 0,
        output_rows=frame.height,
        output_columns=frame.width,
        output_bytes=output_bytes,
        stages_completed=len(result.step_metrics),
        duration_ms=result.duration_ms,
    )


__all__ = [
    "PublicationOutcome",
    "ResultPublisher",
    "cached_outcome",
]
