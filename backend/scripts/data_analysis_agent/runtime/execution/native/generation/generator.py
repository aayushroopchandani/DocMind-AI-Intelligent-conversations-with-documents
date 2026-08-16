"""Seeded synthetic-dataset generation (Phase 9.6).

The generator takes a typed spec — never prose — and produces a table. Its whole
contract is reproducibility: the same spec, seed and generator version must give
byte-identical output on any machine, which is what makes a generated dataset
safe to cache and replay like any other execution input.

Constraints are enforced rather than hoped for. A comparison such as
"cost < revenue" is re-drawn on a deterministic retry stream until it holds or
the attempt budget runs out, and the budget failing is a typed error rather than
a silently invalid table.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from ....models.generation import (
    GenerationComparisonConstraint,
    GenerationNotNullConstraint,
    GenerationUniqueConstraint,
    SyntheticDatasetSpec,
)
from ....models.plans import PlanColumn
from ..schema import frame_schema
from .randomness import column_stream
from .rules import (
    GenerationRuleError,
    apply_null_probability,
    coerce_date,
    generate_values,
)


MAXIMUM_CONSTRAINT_ATTEMPTS = 8
"""Bounded, per 9.6.4. Each attempt advances the stream deterministically, so a
retry is reproducible — the same spec always needs the same number of attempts."""


class GenerationError(ValueError):
    """A spec could not produce a table satisfying its own constraints."""


@dataclass(frozen=True, slots=True)
class GenerationLimits:
    """Caps applied before a single value is produced (9.6.4)."""

    max_rows: int = 100_000
    max_columns: int = 200
    max_categories: int = 500
    max_string_length: int = 240


_COMPARATORS = {
    "less_than": lambda left, right: left < right,
    "less_than_or_equal": lambda left, right: left <= right,
    "greater_than": lambda left, right: left > right,
    "greater_than_or_equal": lambda left, right: left >= right,
}


def generate_dataset(
    spec: SyntheticDatasetSpec,
    *,
    columns: tuple[PlanColumn, ...],
    limits: GenerationLimits | None = None,
) -> pl.DataFrame:
    """Return the table `spec` describes, typed by the plan's declared schema."""

    bounds = limits or GenerationLimits()
    _check_limits(spec, bounds)

    produced: dict[str, list[object]] = {}
    for column in spec.columns:
        produced[column.column_key] = _generate_column(spec, column, produced, 0)

    for attempt in range(1, MAXIMUM_CONSTRAINT_ATTEMPTS + 1):
        unsatisfied = _unsatisfied(spec, produced)
        if not unsatisfied:
            break
        # Redraw only the columns a failing constraint actually touches, so a
        # retry cannot perturb a column that was already correct.
        for key in unsatisfied:
            column = next(
                item for item in spec.columns if item.column_key == key
            )
            produced[key] = _generate_column(spec, column, produced, attempt)
    else:
        raise GenerationError(
            f"generation constraints for '{spec.dataset_name}' did not hold "
            f"after {MAXIMUM_CONSTRAINT_ATTEMPTS} deterministic attempts"
        )

    frame = _build_frame(spec, produced, columns)
    _validate_output(spec, frame, bounds)
    return frame


def _check_limits(spec: SyntheticDatasetSpec, limits: GenerationLimits) -> None:
    if spec.row_count > limits.max_rows:
        raise GenerationError(
            f"generation of {spec.row_count} rows exceeds the limit of "
            f"{limits.max_rows}"
        )
    if len(spec.columns) > limits.max_columns:
        raise GenerationError(
            f"generation of {len(spec.columns)} columns exceeds the limit of "
            f"{limits.max_columns}"
        )
    for column in spec.columns:
        values = getattr(column.rule, "values", ())
        if len(values) > limits.max_categories:
            raise GenerationError(
                f"column '{column.column_key}' declares {len(values)} "
                f"categories, above the limit of {limits.max_categories}"
            )
        for value in values:
            if isinstance(value, str) and len(value) > limits.max_string_length:
                raise GenerationError(
                    f"column '{column.column_key}' declares a category longer "
                    f"than {limits.max_string_length} characters"
                )


def _generate_column(
    spec: SyntheticDatasetSpec,
    column,
    produced: dict[str, list[object]],
    attempt: int,
) -> list[object]:
    stream = column_stream(
        global_seed=spec.seed,
        generator_version=spec.generator_version,
        column_key=column.column_key,
        attempt=attempt,
    )
    try:
        values = generate_values(
            column.rule,
            row_count=spec.row_count,
            stream=stream,
            produced=produced,
        )
    except GenerationRuleError as error:
        raise GenerationError(str(error)) from error
    return apply_null_probability(
        values,
        probability=column.null_probability,
        stream=stream,
    )


def _unsatisfied(
    spec: SyntheticDatasetSpec,
    produced: dict[str, list[object]],
) -> tuple[str, ...]:
    """Return the columns that must be redrawn for a constraint to hold."""

    failing: list[str] = []
    for constraint in spec.constraints:
        if isinstance(constraint, GenerationComparisonConstraint):
            left = produced[constraint.left_column_key]
            right = produced[constraint.right_column_key]
            compare = _COMPARATORS[constraint.operator]
            holds = all(
                lhs is None or rhs is None or compare(lhs, rhs)
                for lhs, rhs in zip(left, right, strict=True)
            )
            if not holds:
                # Redraw the left side; the right side is what it is compared
                # against, and redrawing both would chase a moving target.
                failing.append(constraint.left_column_key)
        elif isinstance(constraint, GenerationUniqueConstraint):
            keys = list(
                zip(
                    *(produced[key] for key in constraint.column_keys),
                    strict=True,
                )
            )
            if len(keys) != len(set(keys)):
                failing.extend(constraint.column_keys)
        elif isinstance(constraint, GenerationNotNullConstraint):
            for key in constraint.column_keys:
                if any(value is None for value in produced[key]):
                    failing.append(key)
    return tuple(dict.fromkeys(failing))


def _build_frame(
    spec: SyntheticDatasetSpec,
    produced: dict[str, list[object]],
    columns: tuple[PlanColumn, ...],
) -> pl.DataFrame:
    schema = frame_schema(columns)
    data: dict[str, list[object]] = {}
    for column in columns:
        values = produced[column.key]
        if schema[column.key] == pl.Date:
            values = [coerce_date(value) for value in values]
        data[column.key] = values
    try:
        return pl.DataFrame(data, schema=schema, strict=False)
    except (pl.exceptions.PolarsError, TypeError, ValueError) as error:
        raise GenerationError(
            f"generated values do not fit the declared schema: {error}"
        ) from error


def _validate_output(
    spec: SyntheticDatasetSpec,
    frame: pl.DataFrame,
    limits: GenerationLimits,
) -> None:
    """Re-check the finished table (9.6.4 post-generation validation).

    The constraint loop works on Python lists; this re-checks the materialized,
    typed frame, so a coercion that quietly changed a value cannot slip through.
    """

    if frame.height != spec.row_count:
        raise GenerationError(
            f"generated {frame.height} rows, expected {spec.row_count}"
        )
    for constraint in spec.constraints:
        if isinstance(constraint, GenerationUniqueConstraint):
            keys = list(constraint.column_keys)
            if frame.select(keys).n_unique() != frame.height:
                raise GenerationError(
                    "generated data violates a unique constraint on "
                    + ", ".join(keys)
                )
        elif isinstance(constraint, GenerationNotNullConstraint):
            for key in constraint.column_keys:
                if frame.get_column(key).null_count():
                    raise GenerationError(
                        f"generated column '{key}' contains nulls despite a "
                        "not-null constraint"
                    )
        elif isinstance(constraint, GenerationComparisonConstraint):
            left = pl.col(constraint.left_column_key)
            right = pl.col(constraint.right_column_key)
            holds = _COMPARATORS[constraint.operator](left, right)
            violations = frame.select(
                holds.fill_null(True).not_().sum().alias("violations")
            ).item()
            if violations:
                raise GenerationError(
                    f"generated data violates {constraint.left_column_key} "
                    f"{constraint.operator} {constraint.right_column_key} on "
                    f"{violations} rows"
                )


__all__ = [
    "MAXIMUM_CONSTRAINT_ATTEMPTS",
    "GenerationError",
    "GenerationLimits",
    "generate_dataset",
]
