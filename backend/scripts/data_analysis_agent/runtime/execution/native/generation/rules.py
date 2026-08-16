"""One value generator per typed rule (Phase 9.6.1).

Every generator is a pure function of (rule, row count, pinned stream) plus, for
dependent rules, the columns generated before it. None of them reads a clock, a
process id, or a global RNG, so the same spec and seed always produce the same
column.

Money is generated in integer minor units and scaled once at the end, so a
currency value is never the result of accumulated binary-float error.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from ....models.generation import (
    BooleanRule,
    CategoricalRule,
    ConstantRule,
    DateRangeRule,
    DecimalRangeRule,
    DependentFractionRule,
    GenerationRule,
    IntegerRangeRule,
    SequenceRule,
    UniqueIdRule,
)


class GenerationRuleError(ValueError):
    """A rule cannot produce values for the requested column."""


MAXIMUM_ID_WIDTH = 24


def generate_values(
    rule: GenerationRule,
    *,
    row_count: int,
    stream: np.random.Generator,
    produced: dict[str, list[object]],
) -> list[object]:
    """Return `row_count` values for one column."""

    if isinstance(rule, SequenceRule):
        return [rule.start + index * rule.step for index in range(row_count)]

    if isinstance(rule, UniqueIdRule):
        # Deterministic and unique by construction: a counter, not a draw.
        return [
            f"{rule.prefix}{rule.start + index:0{rule.width}d}"
            for index in range(row_count)
        ]

    if isinstance(rule, ConstantRule):
        return [rule.value] * row_count

    if isinstance(rule, CategoricalRule):
        weights = None
        if rule.weights is not None:
            total = float(sum(rule.weights))
            weights = [weight / total for weight in rule.weights]
        picks = stream.choice(len(rule.values), size=row_count, p=weights)
        return [rule.values[index] for index in picks]

    if isinstance(rule, BooleanRule):
        draws = stream.random(row_count)
        return [bool(value < rule.probability_true) for value in draws]

    if isinstance(rule, IntegerRangeRule):
        # `high` is exclusive, so add one to make the declared maximum reachable.
        draws = stream.integers(rule.minimum, rule.maximum + 1, size=row_count)
        return [int(value) for value in draws]

    if isinstance(rule, DecimalRangeRule):
        draws = stream.integers(
            rule.minimum_minor_units,
            rule.maximum_minor_units + 1,
            size=row_count,
        )
        factor = 10**rule.scale
        return [round(int(value) / factor, rule.scale) for value in draws]

    if isinstance(rule, DateRangeRule):
        span = (rule.end - rule.start).days
        draws = stream.integers(0, span + 1, size=row_count)
        return [rule.start + timedelta(days=int(value)) for value in draws]

    if isinstance(rule, DependentFractionRule):
        return _dependent_fraction(rule, row_count, stream, produced)

    raise GenerationRuleError(
        f"unsupported generation rule '{type(rule).__name__}'"
    )


def _dependent_fraction(
    rule: DependentFractionRule,
    row_count: int,
    stream: np.random.Generator,
    produced: dict[str, list[object]],
) -> list[object]:
    source = produced.get(rule.source_column_key)
    if source is None:
        raise GenerationRuleError(
            f"dependent rule references ungenerated column "
            f"'{rule.source_column_key}'"
        )
    fractions = stream.uniform(
        rule.minimum_fraction,
        rule.maximum_fraction,
        size=row_count,
    )
    factor = 10**rule.scale
    values: list[object] = []
    for index in range(row_count):
        base = source[index]
        if base is None:
            values.append(None)
            continue
        # Round through integer minor units so the dependent value keeps the
        # same fixed scale as the column it derives from.
        minor = int(round(float(base) * factor * float(fractions[index])))
        values.append(round(minor / factor, rule.scale))
    return values


def apply_null_probability(
    values: list[object],
    *,
    probability: float,
    stream: np.random.Generator,
) -> list[object]:
    """Blank a deterministic subset of values.

    Drawn from the same column stream, after the values themselves, so the
    nulls a column receives do not depend on any other column.
    """

    if probability <= 0:
        return values
    draws = stream.random(len(values))
    return [
        None if draw < probability else value
        for value, draw in zip(values, draws, strict=True)
    ]


def coerce_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


__all__ = [
    "MAXIMUM_ID_WIDTH",
    "GenerationRuleError",
    "apply_null_probability",
    "coerce_date",
    "generate_values",
]
