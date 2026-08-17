"""Run a native recipe. Pure compute: no database, no network, no credentials.

This module is what the bounded child process executes. It reads staged Arrow
IPC files, applies the compiled stages, and writes one Arrow IPC output plus a
manifest. It imports nothing from repositories, storage or the web layer, which
is what makes the "the child cannot access application credentials" guarantee
structural rather than a matter of discipline.

Stage fusion (9.4.3): steps stay lazy and are evaluated in batches. Every
derived quantity in a batch — per-step row counts, semantic guard counters and
the frame that ends it — is collected through one `collect_all`, so Polars
applies common-subplan elimination across all of them instead of re-running the
shared prefix once per step.

A batch ends at a barrier operation (pivot, whose engine API is eager) or at the
final result. Everything between barriers fuses.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from ...models.plans import JoinStep, PlanColumn, PlanStep, step_input_aliases
from ..contracts import (
    ExecutionFailureCode,
    NativeExecutionResult,
    NativeRecipe,
    StepMetrics,
)
from . import semantics
from .expression_compiler import ExpressionCompilationError
from .operations import (
    NativeExecutionSemanticError,
    UnsupportedOperationError,
    check_expansion,
    lookup,
)
from .schema import NativeSchemaError, assert_frame_matches


ENGINE_NAME = "polars"


def engine_version() -> str:
    return f"{ENGINE_NAME}-{pl.__version__}"


class _Failure(Exception):
    def __init__(self, code: ExecutionFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def execute_recipe(recipe: NativeRecipe, *, output_path: Path) -> NativeExecutionResult:
    """Execute `recipe` and write its result to `output_path`."""

    started = time.perf_counter()
    try:
        result, metrics = _run_stages(recipe)
        columns = _result_columns(recipe)
        assert_frame_matches(result, columns)
        _enforce_limits(recipe, result)
        content_hash = result_content_hash(result, columns)
        result.write_ipc(output_path, compression="zstd")
        size = output_path.stat().st_size
        if size > recipe.limits.max_output_bytes:
            raise _Failure(
                ExecutionFailureCode.OUTPUT_TOO_LARGE,
                f"native output is {size} bytes, above the configured limit",
            )
        return NativeExecutionResult(
            succeeded=True,
            engine_version=engine_version(),
            semantics_version=semantics.NATIVE_SEMANTICS_VERSION,
            result_columns=columns,
            row_count=result.height,
            content_hash=content_hash,
            output_bytes=size,
            ipc_path=str(output_path),
            step_metrics=tuple(metrics),
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    except _Failure as failure:
        return _failed(failure.code, failure.message, started)
    except UnsupportedOperationError as error:
        return _failed(ExecutionFailureCode.UNSUPPORTED_OPERATION, str(error), started)
    except ExpressionCompilationError as error:
        return _failed(ExecutionFailureCode.COMPILATION_FAILED, str(error), started)
    except NativeSchemaError as error:
        return _failed(ExecutionFailureCode.SCHEMA_MISMATCH, str(error), started)
    except NativeExecutionSemanticError as error:
        return _failed(ExecutionFailureCode.SEMANTIC_VIOLATION, str(error), started)
    except pl.exceptions.PolarsError as error:
        # A cardinality violation surfaces from `join(validate=...)` as a
        # ComputeError; it is a semantic failure, not an engine crash.
        code = (
            ExecutionFailureCode.SEMANTIC_VIOLATION
            if "validation" in str(error).casefold()
            else ExecutionFailureCode.ENGINE_CRASHED
        )
        return _failed(code, str(error), started)


def _failed(
    code: ExecutionFailureCode,
    message: str,
    started: float,
) -> NativeExecutionResult:
    return NativeExecutionResult(
        succeeded=False,
        engine_version=engine_version(),
        semantics_version=semantics.NATIVE_SEMANTICS_VERSION,
        failure_code=code,
        # Messages describe shapes and identifiers, never cell values.
        failure_message=message[:1_000],
        duration_ms=(time.perf_counter() - started) * 1000,
    )


@dataclass(slots=True)
class _Batch:
    """Lazy plans awaiting one shared evaluation."""

    plans: list[pl.LazyFrame] = field(default_factory=list)
    guards: list[tuple[str, int]] = field(default_factory=list)
    counts: list[tuple[PlanStep, str, int, int]] = field(default_factory=list)

    def add_guard(self, step_id: str, plan: pl.LazyFrame) -> None:
        self.guards.append((step_id, len(self.plans)))
        self.plans.append(plan)

    def add_count(
        self,
        step: PlanStep,
        source_alias: str,
        width: int,
        plan: pl.LazyFrame,
    ) -> None:
        self.counts.append((step, source_alias, width, len(self.plans)))
        self.plans.append(plan)


def _run_stages(recipe: NativeRecipe) -> tuple[pl.DataFrame, list[StepMetrics]]:
    lazy: dict[str, pl.LazyFrame] = {}
    row_counts: dict[str, int] = {}
    for table in recipe.inputs:
        frame = pl.read_ipc(table.ipc_path)
        assert_frame_matches(frame, table.columns)
        lazy[table.alias] = frame.lazy()
        row_counts[table.alias] = frame.height

    metrics: list[StepMetrics] = []
    batch = _Batch()

    def flush(tail: pl.LazyFrame | None) -> pl.DataFrame | None:
        """Evaluate the pending batch, plus one frame the caller needs now."""

        plans = list(batch.plans)
        if tail is not None:
            plans.append(tail)
        evaluated = pl.collect_all(plans) if plans else []
        for step_id, index in batch.guards:
            _raise_for_guard(step_id, evaluated[index])
        for step, source_alias, width, index in batch.counts:
            counted = evaluated[index]
            rows = int(counted.item()) if counted.height else 0
            _check_row_limit(recipe, rows)
            inputs = row_counts.get(source_alias, 0)
            if isinstance(step, JoinStep):
                check_expansion(step, inputs, rows)
            metrics.append(
                StepMetrics(
                    step_id=step.step_id,
                    kind=step.kind,
                    input_rows=inputs,
                    output_rows=rows,
                    output_columns=width,
                )
            )
            row_counts[step.output_alias] = rows
        batch.plans.clear()
        batch.guards.clear()
        batch.counts.clear()
        return evaluated[-1] if tail is not None else None

    for step in recipe.steps:
        operation = lookup(step.kind)
        aliases = step_input_aliases(step)
        missing = [alias for alias in aliases if alias not in lazy]
        if missing:
            raise _Failure(
                ExecutionFailureCode.INPUT_UNAVAILABLE,
                f"step '{step.step_id}' reads unknown alias '{missing[0]}'",
            )
        # A source operation such as `generate_dataset` reads nothing.
        primary = aliases[0] if aliases else None

        if operation.guards is not None and primary is not None:
            available = frozenset(lazy[primary].collect_schema().names())
            for expression in operation.guards(step, available):
                batch.add_guard(step.step_id, lazy[primary].select(expression))

        if operation.barrier:
            # The operation needs real data, so the batch ends here.
            source = flush(lazy[primary] if primary is not None else None)
            frames = {primary: source} if primary is not None else {}
            produced = operation.apply(step, frames)
            lazy[step.output_alias] = produced.lazy()
            _check_row_limit(recipe, produced.height)
            metrics.append(
                StepMetrics(
                    step_id=step.step_id,
                    kind=step.kind,
                    input_rows=row_counts.get(primary, 0) if primary else 0,
                    output_rows=produced.height,
                    output_columns=produced.width,
                )
            )
            row_counts[step.output_alias] = produced.height
            continue

        produced = operation.apply(step, {alias: lazy[alias] for alias in aliases})
        lazy[step.output_alias] = produced
        # A join's ratio is measured against its larger input, which is the one
        # a bad key distribution multiplies.
        source_alias = (
            max(aliases, key=lambda alias: row_counts.get(alias, 0))
            if isinstance(step, JoinStep)
            else primary
        )
        batch.add_count(
            step,
            source_alias,
            len(produced.collect_schema().names()),
            produced.select(pl.len().alias("rows")),
        )

    result = flush(lazy[recipe.result_alias])
    assert result is not None
    metrics.sort(key=lambda item: _step_order(recipe, item.step_id))
    return result, metrics


def _step_order(recipe: NativeRecipe, step_id: str) -> int:
    for index, step in enumerate(recipe.steps):
        if step.step_id == step_id:
            return index
    return len(recipe.steps)  # pragma: no cover - metrics always match a step


def _check_row_limit(recipe: NativeRecipe, rows: int) -> None:
    if rows > recipe.limits.max_output_rows:
        raise _Failure(
            ExecutionFailureCode.ROW_LIMIT_EXCEEDED,
            f"a stage produced {rows} rows, above the configured limit",
        )


_GUARD_MESSAGES = (
    (
        "null_predicate_rows",
        "declared null_predicate_policy='error' and {count} rows evaluated to null",
    ),
    (
        "zero_divisor_",
        "declared zero_division='error' and {count} rows divide by zero",
    ),
    (
        "aggregate_null_",
        "declared null_policy='error' and {count} aggregated values are null",
    ),
    (
        "duplicate_rows",
        "declared keep='error' and {count} rows share a duplicate key",
    ),
)


def _raise_for_guard(step_id: str, counted: pl.DataFrame) -> None:
    counts = counted.row(0, named=True) if counted.height else {}
    for name, count in counts.items():
        if not count:
            continue
        for prefix, template in _GUARD_MESSAGES:
            if name.startswith(prefix):
                raise _Failure(
                    ExecutionFailureCode.SEMANTIC_VIOLATION,
                    f"step '{step_id}' " + template.format(count=count),
                )
        raise _Failure(  # pragma: no cover - a guard without a message is a bug
            ExecutionFailureCode.SEMANTIC_VIOLATION,
            f"step '{step_id}' violated declared policy '{name}'",
        )


def _result_columns(recipe: NativeRecipe) -> tuple[PlanColumn, ...]:
    for step in recipe.steps:
        if step.output_alias == recipe.result_alias:
            return step.expected_schema
    for table in recipe.inputs:
        if table.alias == recipe.result_alias:
            return table.columns
    raise _Failure(
        ExecutionFailureCode.SCHEMA_MISMATCH,
        "the recipe result alias has no declared schema",
    )


def _enforce_limits(recipe: NativeRecipe, frame: pl.DataFrame) -> None:
    cells = frame.height * frame.width
    if cells > recipe.limits.max_output_cells:
        raise _Failure(
            ExecutionFailureCode.CELL_LIMIT_EXCEEDED,
            f"native output has {cells} cells, above the configured limit",
        )
    if not semantics.ALLOW_NON_FINITE_FLOATS:
        for name, dtype in frame.schema.items():
            if dtype not in (pl.Float32, pl.Float64):
                continue
            column = frame.get_column(name)
            if column.is_infinite().any() or column.is_nan().any():
                raise _Failure(
                    ExecutionFailureCode.SEMANTIC_VIOLATION,
                    f"column '{name}' contains non-finite values",
                )


def result_content_hash(
    frame: pl.DataFrame,
    columns: tuple[PlanColumn, ...],
) -> str:
    """Hash schema plus row bytes so replay can be proven identical.

    The schema is hashed from the logical plan columns, not from the physical
    dtypes, so a currency column and a plain decimal column with identical
    numbers do not collide.
    """

    digest = hashlib.sha256()
    for column in columns:
        digest.update(column.key.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(column.data_type.value.encode("ascii"))
        digest.update(b"\x00")
        digest.update((column.unit or "").encode("utf-8"))
        digest.update(b"\x1e")
    digest.update(frame.height.to_bytes(8, "big"))
    # Arrow IPC bytes are stable for a pinned engine version, and the engine
    # version is part of the execution key, so this is a safe canonical form.
    payload = frame.serialize(format="binary")
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
    return digest.hexdigest()


__all__ = [
    "ENGINE_NAME",
    "engine_version",
    "execute_recipe",
    "result_content_hash",
]
