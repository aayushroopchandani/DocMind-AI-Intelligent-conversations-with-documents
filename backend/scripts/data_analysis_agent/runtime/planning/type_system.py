"""The single deterministic type system shared by every plan validation layer.

Phase 9.2 turns each executable value into a closed schema union, which is only
meaningful when one question has one answer. Before this module the runtime
carried two answers: the expression checker treated every numeric type as
interchangeable and parsed date literals strictly, while the column checker
treated currency and percentage as distinct semantic types and accepted any
string as a date.

Both callers now share these rules:

* ``currency`` and ``percentage`` are semantic types that only match themselves
  (a Phase 8 rule that join keys, aggregates and fill rules already relied on);
* every other numeric type is mutually compatible;
* compatibility additionally requires an exactly equal unit;
* literals are checked strictly, so an integer column rejects ``1.5`` and a date
  column rejects a string that is not an ISO date.

``unknown`` stays permissive on purpose: it means the upstream profiler could
not infer a type, and the referential layer already reports it separately.
"""

from __future__ import annotations

from datetime import date

from ..models.plans import PlanColumn, PlanDataType


NUMERIC_TYPES = frozenset(
    {
        PlanDataType.INTEGER,
        PlanDataType.NUMBER,
        PlanDataType.DECIMAL,
        PlanDataType.CURRENCY,
        PlanDataType.PERCENTAGE,
    }
)
SEMANTIC_NUMERIC_TYPES = frozenset(
    {
        PlanDataType.CURRENCY,
        PlanDataType.PERCENTAGE,
    }
)
TEMPORAL_TYPES = frozenset({PlanDataType.DATE, PlanDataType.PERIOD})
ORDERABLE_TYPES = NUMERIC_TYPES | TEMPORAL_TYPES

_NUMERIC_WIDTH_ORDER = (
    PlanDataType.INTEGER,
    PlanDataType.DECIMAL,
    PlanDataType.NUMBER,
)


def types_compatible(
    left_type: PlanDataType,
    left_unit: str | None,
    right_type: PlanDataType,
    right_unit: str | None,
) -> bool:
    """Return whether two typed values may be compared or merged."""

    if PlanDataType.UNKNOWN in {left_type, right_type}:
        return False
    if left_type in SEMANTIC_NUMERIC_TYPES or right_type in SEMANTIC_NUMERIC_TYPES:
        type_compatible = left_type == right_type
    else:
        type_compatible = left_type == right_type or (
            left_type in NUMERIC_TYPES and right_type in NUMERIC_TYPES
        )
    return type_compatible and left_unit == right_unit


def columns_compatible(left: PlanColumn, right: PlanColumn) -> bool:
    """Return whether two schema columns share one comparable type and unit."""

    return types_compatible(
        left.data_type,
        left.unit,
        right.data_type,
        right.unit,
    )


def literal_matches(value: object, data_type: PlanDataType) -> bool:
    """Return whether a JSON literal is a valid member of ``data_type``.

    ``None`` is always accepted: nullability is a separate contract enforced by
    the column and generation validators.
    """

    if value is None:
        return True
    if data_type == PlanDataType.UNKNOWN:
        # The profiler could not infer a type; the referential layer reports it.
        return True
    if data_type == PlanDataType.BOOLEAN:
        return isinstance(value, bool)
    if data_type == PlanDataType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type in NUMERIC_TYPES:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if data_type == PlanDataType.DATE:
        if isinstance(value, date):
            return True
        if not isinstance(value, str):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if data_type == PlanDataType.PERIOD:
        return isinstance(value, str) and bool(value.strip())
    if data_type == PlanDataType.STRING:
        return isinstance(value, str)
    return False


def wider_numeric_type(
    left: PlanDataType,
    right: PlanDataType,
) -> PlanDataType:
    """Return the arithmetic result type for two numeric operands.

    Semantic types survive arithmetic: subtracting one currency from another,
    or scaling a currency by a plain factor, still yields currency. Mixing two
    different semantic types is meaningless and yields ``unknown`` so the
    caller reports a type mismatch.
    """

    semantic = {left, right}.intersection(SEMANTIC_NUMERIC_TYPES)
    if semantic:
        if len(semantic) > 1:
            return PlanDataType.UNKNOWN
        other = right if left in semantic else left
        if other not in NUMERIC_TYPES:
            return PlanDataType.UNKNOWN
        return next(iter(semantic))
    if not all(item in _NUMERIC_WIDTH_ORDER for item in (left, right)):
        return PlanDataType.UNKNOWN
    return max((left, right), key=_NUMERIC_WIDTH_ORDER.index)


__all__ = [
    "NUMERIC_TYPES",
    "ORDERABLE_TYPES",
    "SEMANTIC_NUMERIC_TYPES",
    "TEMPORAL_TYPES",
    "columns_compatible",
    "literal_matches",
    "types_compatible",
    "wider_numeric_type",
]
