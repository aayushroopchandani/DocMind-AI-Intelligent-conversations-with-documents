"""Run a native recipe. Pure compute: no database, no network, no credentials.

This module is what the bounded child process executes. It reads staged Arrow
IPC files, applies the compiled stages, and writes one Arrow IPC output plus a
manifest. It imports nothing from repositories, storage or the web layer, which
is what makes the "the child cannot access application credentials" guarantee
structural rather than a matter of discipline.

Stage fusion (9.4.3): a linear chain of steps stays inside one `LazyFrame`, so
Polars performs its own projection and predicate pushdown and only one
materialization happens per branch point. Per-step row metrics are still
reported, because the user sees logical steps even when several ran as one
query.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from ...models.plans import PlanColumn, PlanStep, step_input_aliases
from ..contracts import (
    ExecutionFailureCode,
    NativeExecutionResult,
    NativeRecipe,
    StepMetrics,
)
from . import semantics
from .expression_compiler import ExpressionCompilationError
from .operation_compiler import (
    NativeExecutionSemanticError,
    UnsupportedOperationError,
    aggregate_null_guards,
    compile_step,
    null_predicate_guard,
    zero_division_guards,
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
        frames, metrics = _run_stages(recipe)
        result = frames[recipe.result_alias]
        columns = _result_columns(recipe)
        assert_frame_matches(result, columns)
        _enforce_limits(recipe, result)
        content_hash = _content_hash(result, columns)
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
    except (UnsupportedOperationError,) as error:
        return _failed(ExecutionFailureCode.UNSUPPORTED_OPERATION, str(error), started)
    except (ExpressionCompilationError,) as error:
        return _failed(ExecutionFailureCode.COMPILATION_FAILED, str(error), started)
    except (NativeSchemaError,) as error:
        return _failed(ExecutionFailureCode.SCHEMA_MISMATCH, str(error), started)
    except (NativeExecutionSemanticError,) as error:
        return _failed(ExecutionFailureCode.SEMANTIC_VIOLATION, str(error), started)
    except pl.exceptions.PolarsError as error:
        return _failed(ExecutionFailureCode.ENGINE_CRASHED, str(error), started)


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


def _run_stages(
    recipe: NativeRecipe,
) -> tuple[dict[str, pl.DataFrame], list[StepMetrics]]:
    """Build the whole lazy graph, then evaluate it in one optimized pass.

    Every derived quantity — per-step row counts, semantic guard counters and
    the final table — is collected together through `collect_all`, so Polars
    applies common-subplan elimination once across all of them. Collecting them
    separately would re-run the shared prefix for each step and turn a fused
    chain into quadratic work.
    """

    lazy: dict[str, pl.LazyFrame] = {}
    input_rows: dict[str, int] = {}
    for table in recipe.inputs:
        frame = pl.read_ipc(table.ipc_path)
        assert_frame_matches(frame, table.columns)
        lazy[table.alias] = frame.lazy()
        input_rows[table.alias] = frame.height

    plans: list[pl.LazyFrame] = []
    guards: list[_Guard] = []
    stages: list[_Stage] = []

    for step in recipe.steps:
        inputs = step_input_aliases(step)
        if len(inputs) != 1:
            raise _Failure(
                ExecutionFailureCode.UNSUPPORTED_OPERATION,
                f"step '{step.step_id}' needs {len(inputs)} inputs; the capped "
                "native engine executes single-input operations only",
            )
        alias = inputs[0]
        if alias not in lazy:
            raise _Failure(
                ExecutionFailureCode.INPUT_UNAVAILABLE,
                f"step '{step.step_id}' reads unknown alias '{alias}'",
            )
        source = lazy[alias]
        available = frozenset(source.collect_schema().names())
        for expression in _guard_expressions(step, available):
            guards.append(_Guard(step.step_id, len(plans)))
            plans.append(source.select(expression))
        produced = compile_step(step, source, available_columns=available)
        lazy[step.output_alias] = produced
        stages.append(
            _Stage(
                step=step,
                source_alias=alias,
                length_index=len(plans),
                output_columns=len(produced.collect_schema().names()),
            )
        )
        plans.append(produced.select(pl.len().alias("rows")))

    result_index = len(plans)
    plans.append(lazy[recipe.result_alias])
    evaluated = pl.collect_all(plans)

    for guard in guards:
        _raise_for_guard(guard, evaluated[guard.index])

    row_counts = dict(input_rows)
    metrics: list[StepMetrics] = []
    for stage in stages:
        counted = evaluated[stage.length_index]
        rows = int(counted.item()) if counted.height else 0
        if rows > recipe.limits.max_output_rows:
            raise _Failure(
                ExecutionFailureCode.ROW_LIMIT_EXCEEDED,
                f"a stage produced {rows} rows, above the configured limit",
            )
        metrics.append(
            StepMetrics(
                step_id=stage.step.step_id,
                kind=stage.step.kind,
                input_rows=row_counts.get(stage.source_alias, 0),
                output_rows=rows,
                output_columns=stage.output_columns,
            )
        )
        row_counts[stage.step.output_alias] = rows

    return {recipe.result_alias: evaluated[result_index]}, metrics


@dataclass(frozen=True, slots=True)
class _Stage:
    step: PlanStep
    source_alias: str
    length_index: int
    output_columns: int


@dataclass(frozen=True, slots=True)
class _Guard:
    step_id: str
    index: int


def _guard_expressions(
    step: PlanStep,
    available: frozenset[str],
) -> tuple[pl.Expr, ...]:
    """Return counters for policies an expression cannot raise from itself.

    A Polars expression has no way to abort, so every "…='error'" policy in the
    plan becomes a counter evaluated alongside the stage. A non-zero counter
    fails the run before anything is published.
    """

    if step.kind == "filter_rows":
        guard = null_predicate_guard(step, available)
        return (guard,) if guard is not None else ()
    if step.kind == "derive_column":
        return zero_division_guards(step, available)
    if step.kind == "aggregate":
        return aggregate_null_guards(step)
    return ()


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
)


def _raise_for_guard(guard: _Guard, counted: pl.DataFrame) -> None:
    counts = counted.row(0, named=True) if counted.height else {}
    for name, count in counts.items():
        if not count:
            continue
        for prefix, template in _GUARD_MESSAGES:
            if name.startswith(prefix):
                raise _Failure(
                    ExecutionFailureCode.SEMANTIC_VIOLATION,
                    f"step '{guard.step_id}' "
                    + template.format(count=count),
                )
        raise _Failure(  # pragma: no cover - a guard without a message is a bug
            ExecutionFailureCode.SEMANTIC_VIOLATION,
            f"step '{guard.step_id}' violated declared policy '{name}'",
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


def _content_hash(frame: pl.DataFrame, columns: tuple[PlanColumn, ...]) -> str:
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
]
