"""The six validation layers a result passes before publication (Phase 9.9.2).

Ordered cheapest and most fundamental first, so a protocol mismatch is reported
as a protocol mismatch rather than surfacing later as a confusing assertion
failure. Every layer returns typed issues instead of raising, so a caller can
report all the problems with a result at once.

Nothing here trusts the worker. The child process returns a manifest; this
recomputes what the manifest claims from the bytes that actually arrived.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from ....runtime.models.plans import (
    PlanAssertion,
    PlanAssertionKind,
    PlanColumn,
    PlanDataType,
)
from ...formulas.safety import is_injection_risk
from ..contracts import ExecutionFailureCode, ExecutionLimits, NativeExecutionResult
from ..native.schema import frame_schema


@dataclass(frozen=True, slots=True)
class ResultIssue:
    code: ExecutionFailureCode
    message: str
    layer: str


def validate_result(
    *,
    result: NativeExecutionResult,
    frame: pl.DataFrame,
    declared_columns: tuple[PlanColumn, ...],
    assertions: tuple[PlanAssertion, ...],
    limits: ExecutionLimits,
    recomputed_hash: str,
    workbook_bound: bool = False,
) -> tuple[ResultIssue, ...]:
    """Run every layer and return the problems found, in layer order."""

    issues: list[ResultIssue] = []
    issues.extend(_protocol(result, frame))
    issues.extend(_schema(frame, declared_columns))
    issues.extend(_assertions(frame, assertions))
    issues.extend(_resources(frame, limits))
    if workbook_bound:
        issues.extend(_safety(frame, declared_columns))
    issues.extend(_hashes(result, recomputed_hash))
    return tuple(issues)


def _issue(code: ExecutionFailureCode, message: str, layer: str) -> ResultIssue:
    return ResultIssue(code=code, message=message, layer=layer)


def _protocol(
    result: NativeExecutionResult,
    frame: pl.DataFrame,
) -> tuple[ResultIssue, ...]:
    """Layer 1 — the worker's response matches the file it produced."""

    issues: list[ResultIssue] = []
    if not result.succeeded:
        issues.append(
            _issue(
                result.failure_code or ExecutionFailureCode.ENGINE_CRASHED,
                result.failure_message or "native execution failed",
                "protocol",
            )
        )
        return tuple(issues)
    if result.row_count != frame.height:
        issues.append(
            _issue(
                ExecutionFailureCode.SCHEMA_MISMATCH,
                f"manifest claims {result.row_count} rows but the file has "
                f"{frame.height}",
                "protocol",
            )
        )
    if len(result.result_columns) != frame.width:
        issues.append(
            _issue(
                ExecutionFailureCode.SCHEMA_MISMATCH,
                f"manifest claims {len(result.result_columns)} columns but the "
                f"file has {frame.width}",
                "protocol",
            )
        )
    return tuple(issues)


def _schema(
    frame: pl.DataFrame,
    declared: tuple[PlanColumn, ...],
) -> tuple[ResultIssue, ...]:
    """Layer 2 — the output matches the schema the plan declared."""

    expected = frame_schema(declared)
    actual = dict(frame.schema)
    if list(actual) != list(expected):
        return (
            _issue(
                ExecutionFailureCode.SCHEMA_MISMATCH,
                f"output columns {list(actual)} do not match the declared "
                f"{list(expected)}",
                "schema",
            ),
        )
    issues = [
        _issue(
            ExecutionFailureCode.SCHEMA_MISMATCH,
            f"column '{key}' is {actual[key]} but was declared {dtype}",
            "schema",
        )
        for key, dtype in expected.items()
        if actual[key] != dtype
    ]
    issues.extend(
        _issue(
            ExecutionFailureCode.SCHEMA_MISMATCH,
            f"column '{column.key}' was declared non-nullable but contains nulls",
            "schema",
        )
        for column in declared
        if not column.nullable and frame.get_column(column.key).null_count()
    )
    return tuple(issues)


def _assertions(
    frame: pl.DataFrame,
    assertions: tuple[PlanAssertion, ...],
) -> tuple[ResultIssue, ...]:
    """Layer 3 — the assertions the plan attached to its own output."""

    issues: list[ResultIssue] = []
    available = set(frame.columns)
    for assertion in assertions:
        missing = [key for key in assertion.columns if key not in available]
        if missing:
            issues.append(
                _issue(
                    ExecutionFailureCode.ASSERTION_FAILED,
                    "assertion references missing columns: "
                    + ", ".join(sorted(missing)),
                    "assertion",
                )
            )
            continue
        issues.extend(_check_assertion(frame, assertion))
    return tuple(issues)


def _check_assertion(
    frame: pl.DataFrame,
    assertion: PlanAssertion,
) -> tuple[ResultIssue, ...]:
    kind = assertion.kind
    if kind is PlanAssertionKind.ROW_COUNT_AT_MOST:
        if assertion.maximum_rows is not None and frame.height > assertion.maximum_rows:
            return (
                _issue(
                    ExecutionFailureCode.ASSERTION_FAILED,
                    f"output has {frame.height} rows, above the asserted "
                    f"maximum of {assertion.maximum_rows}",
                    "assertion",
                ),
            )
        return ()
    if kind is PlanAssertionKind.SCHEMA_CONTAINS:
        return ()  # membership was already proven by the column check above.
    if kind is PlanAssertionKind.NO_NULLS:
        return tuple(
            _issue(
                ExecutionFailureCode.ASSERTION_FAILED,
                f"column '{key}' was asserted non-null but contains nulls",
                "assertion",
            )
            for key in assertion.columns
            if frame.get_column(key).null_count()
        )
    if kind is PlanAssertionKind.UNIQUE:
        keys = list(assertion.columns)
        if frame.select(keys).n_unique() != frame.height:
            return (
                _issue(
                    ExecutionFailureCode.ASSERTION_FAILED,
                    "asserted unique columns contain duplicates: "
                    + ", ".join(keys),
                    "assertion",
                ),
            )
        return ()
    if kind is PlanAssertionKind.VALUE_RANGE:
        issues: list[ResultIssue] = []
        for key in assertion.columns:
            column = frame.get_column(key)
            if assertion.minimum_value is not None:
                below = column.lt(assertion.minimum_value).sum()
                if below:
                    issues.append(
                        _issue(
                            ExecutionFailureCode.ASSERTION_FAILED,
                            f"column '{key}' has {below} values below the "
                            "asserted minimum",
                            "assertion",
                        )
                    )
            if assertion.maximum_value is not None:
                above = column.gt(assertion.maximum_value).sum()
                if above:
                    issues.append(
                        _issue(
                            ExecutionFailureCode.ASSERTION_FAILED,
                            f"column '{key}' has {above} values above the "
                            "asserted maximum",
                            "assertion",
                        )
                    )
        return tuple(issues)
    return ()


def _resources(
    frame: pl.DataFrame,
    limits: ExecutionLimits,
) -> tuple[ResultIssue, ...]:
    """Layer 4 — the output is inside its cell and row budget."""

    issues: list[ResultIssue] = []
    if frame.height > limits.max_output_rows:
        issues.append(
            _issue(
                ExecutionFailureCode.ROW_LIMIT_EXCEEDED,
                f"output has {frame.height} rows, above the configured limit",
                "resource",
            )
        )
    cells = frame.height * frame.width
    if cells > limits.max_output_cells:
        issues.append(
            _issue(
                ExecutionFailureCode.CELL_LIMIT_EXCEEDED,
                f"output has {cells} cells, above the configured limit",
                "resource",
            )
        )
    return tuple(issues)


def _safety(
    frame: pl.DataFrame,
    declared: tuple[PlanColumn, ...],
) -> tuple[ResultIssue, ...]:
    """Layer 5 — nothing workbook-bound would become a live formula.

    Only applied to results headed for a spreadsheet. A value like `=cmd|...`
    is perfectly valid data in an exported CSV, and becomes an attack the moment
    it is written into a cell.
    """

    text_columns = [
        column.key
        for column in declared
        if column.data_type in {PlanDataType.STRING, PlanDataType.PERIOD}
    ]
    issues: list[ResultIssue] = []
    for key in text_columns:
        risky = sum(
            1
            for value in frame.get_column(key).to_list()
            if is_injection_risk(value)
        )
        if risky:
            issues.append(
                _issue(
                    ExecutionFailureCode.SEMANTIC_VIOLATION,
                    f"column '{key}' has {risky} values that a spreadsheet "
                    "would read as formulas",
                    "safety",
                )
            )
    return tuple(issues)


def _hashes(
    result: NativeExecutionResult,
    recomputed: str,
) -> tuple[ResultIssue, ...]:
    """Layer 6 — the content hash the worker claimed is the one we compute."""

    if result.content_hash and result.content_hash != recomputed:
        return (
            _issue(
                ExecutionFailureCode.SCHEMA_MISMATCH,
                "the worker's content hash does not match the published bytes",
                "hash",
            ),
        )
    return ()


__all__ = ["ResultIssue", "validate_result"]
