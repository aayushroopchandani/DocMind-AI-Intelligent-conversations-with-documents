"""Typed models exchanged across the execution boundary.

The recipe is the only thing that crosses into the bounded worker process. It
carries validated plan content and staged input paths — never a database
handle, a credential, or an LLM client. Everything the child returns is a
manifest that the parent re-validates before anything is published.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models.plans import PlanColumn, PlanStep


NATIVE_RECIPE_VERSION = "1.0"

# The operations the Phase 9.4 engine can execute. Declared here, in a module
# that does not import the engine, so admission and persistence can ask "is this
# executable?" without pulling Polars into the request path.
NATIVE_SUPPORTED_OPERATIONS: frozenset[str] = frozenset(
    {
        "filter_rows",
        "select_columns",
        "sort_rows",
        "aggregate",
        "derive_column",
    }
)


class ExecutionFailureCode(str, Enum):
    """Typed failure reasons. Every one is actionable and non-retryable unless
    stated, so the worker never loops on a deterministic error."""

    INPUT_UNAVAILABLE = "input_unavailable"
    INPUT_VERSION_MISMATCH = "input_version_mismatch"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    COMPILATION_FAILED = "compilation_failed"
    SEMANTIC_VIOLATION = "semantic_violation"
    SCHEMA_MISMATCH = "schema_mismatch"
    ROW_LIMIT_EXCEEDED = "row_limit_exceeded"
    CELL_LIMIT_EXCEEDED = "cell_limit_exceeded"
    OUTPUT_TOO_LARGE = "output_too_large"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ASSERTION_FAILED = "assertion_failed"
    ENGINE_CRASHED = "engine_crashed"


class ExecutionLimits(BaseModel):
    """Bounds the parent enforces on the child, independent of plan estimates."""

    max_output_rows: int = Field(default=1_000_000, ge=1)
    max_output_cells: int = Field(default=10_000_000, ge=1)
    max_output_bytes: int = Field(default=64 * 1024 * 1024, ge=1024)
    wall_clock_seconds: float = Field(default=120.0, gt=0, le=3600)

    model_config = ConfigDict(extra="forbid", frozen=True)


class NativeInputTable(BaseModel):
    """One resolved, immutable input staged for the engine."""

    alias: str = Field(min_length=1, max_length=120)
    dataset_id: str = Field(min_length=1, max_length=240)
    content_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    columns: tuple[PlanColumn, ...] = Field(min_length=1, max_length=500)
    row_count: int = Field(ge=0)
    # Absolute path to an Arrow IPC file inside the run's private staging
    # directory. The child receives paths, never storage credentials.
    ipc_path: str = Field(min_length=1, max_length=4096)

    model_config = ConfigDict(extra="forbid", frozen=True)


class NativeRecipe(BaseModel):
    """The complete, self-contained description of one native execution."""

    recipe_version: Literal[NATIVE_RECIPE_VERSION] = NATIVE_RECIPE_VERSION
    engine_version: str = Field(min_length=1, max_length=60)
    semantics_version: str = Field(min_length=1, max_length=60)
    steps: tuple[PlanStep, ...] = Field(min_length=1, max_length=64)
    inputs: tuple[NativeInputTable, ...] = Field(min_length=1, max_length=30)
    result_alias: str = Field(min_length=1, max_length=120)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_recipe(self) -> Self:
        aliases = tuple(item.alias for item in self.inputs)
        if len(aliases) != len(set(aliases)):
            raise ValueError("native input aliases must be unique")
        produced = {step.output_alias for step in self.steps}
        if self.result_alias not in produced and self.result_alias not in aliases:
            raise ValueError("result alias is not produced by the recipe")
        return self


class StepMetrics(BaseModel):
    """Per-step counters. Values are never included, only shapes."""

    step_id: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=60)
    input_rows: int = Field(ge=0)
    output_rows: int = Field(ge=0)
    output_columns: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @property
    def removed_rows(self) -> int:
        return max(0, self.input_rows - self.output_rows)


class NativeExecutionResult(BaseModel):
    """What the child returns and the parent verifies before publishing."""

    succeeded: bool
    engine_version: str = Field(min_length=1, max_length=60)
    semantics_version: str = Field(min_length=1, max_length=60)
    result_columns: tuple[PlanColumn, ...] = Field(default=(), max_length=500)
    row_count: int = Field(default=0, ge=0)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    output_bytes: int = Field(default=0, ge=0)
    ipc_path: str | None = Field(default=None, max_length=4096)
    step_metrics: tuple[StepMetrics, ...] = Field(default=(), max_length=64)
    duration_ms: float = Field(default=0, ge=0)
    failure_code: ExecutionFailureCode | None = None
    failure_message: str | None = Field(default=None, max_length=1_000)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.succeeded:
            if self.failure_code is not None:
                raise ValueError("a successful execution cannot carry a failure")
            if not self.result_columns or self.content_hash is None:
                raise ValueError("a successful execution needs a hashed result")
        elif self.failure_code is None:
            raise ValueError("a failed execution requires a typed failure code")
        return self


__all__ = [
    "NATIVE_RECIPE_VERSION",
    "NATIVE_SUPPORTED_OPERATIONS",
    "ExecutionFailureCode",
    "ExecutionLimits",
    "NativeExecutionResult",
    "NativeInputTable",
    "NativeRecipe",
    "StepMetrics",
]
