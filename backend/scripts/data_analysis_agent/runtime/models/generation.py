from __future__ import annotations

import math
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


GENERATOR_VERSION = "1.0"


class SequenceRule(BaseModel):
    kind: Literal["sequence"] = "sequence"
    start: int = 1
    step: int = 1

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("step")
    @classmethod
    def reject_zero_step(cls, value: int) -> int:
        if value == 0:
            raise ValueError("sequence step cannot be zero")
        return value


class UniqueIdRule(BaseModel):
    kind: Literal["unique_id"] = "unique_id"
    prefix: str = Field(default="id_", max_length=32)
    start: int = Field(default=1, ge=0)
    width: int = Field(default=6, ge=1, le=24)

    model_config = ConfigDict(extra="forbid", frozen=True)


class CategoricalRule(BaseModel):
    kind: Literal["categorical"] = "categorical"
    values: tuple[JsonValue, ...] = Field(min_length=1, max_length=500)
    weights: tuple[float, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_weights(self) -> Self:
        if self.weights is None:
            return self
        if len(self.values) != len(self.weights):
            raise ValueError("categorical values and weights must align")
        if (
            any(not math.isfinite(weight) or weight < 0 for weight in self.weights)
            or sum(self.weights) <= 0
        ):
            raise ValueError(
                "categorical weights must be finite, non-negative, and non-zero"
            )
        return self


class IntegerRangeRule(BaseModel):
    kind: Literal["integer_range"] = "integer_range"
    minimum: int
    maximum: int
    distribution: Literal["uniform"] = "uniform"

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("integer range minimum cannot exceed maximum")
        return self


class DecimalRangeRule(BaseModel):
    kind: Literal["decimal_range"] = "decimal_range"
    minimum_minor_units: int
    maximum_minor_units: int
    scale: int = Field(default=2, ge=0, le=12)
    distribution: Literal["uniform"] = "uniform"

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.minimum_minor_units > self.maximum_minor_units:
            raise ValueError("decimal range minimum cannot exceed maximum")
        return self


class DateRangeRule(BaseModel):
    kind: Literal["date_range"] = "date_range"
    start: date
    end: date
    distribution: Literal["uniform"] = "uniform"

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.start > self.end:
            raise ValueError("date range start cannot follow end")
        return self


class BooleanRule(BaseModel):
    kind: Literal["boolean"] = "boolean"
    probability_true: float = Field(default=0.5, ge=0, le=1)

    model_config = ConfigDict(extra="forbid", frozen=True)


class ConstantRule(BaseModel):
    kind: Literal["constant"] = "constant"
    value: JsonValue

    model_config = ConfigDict(extra="forbid", frozen=True)


class DependentFractionRule(BaseModel):
    """Generate a bounded fraction of an earlier numeric generated column."""

    kind: Literal["dependent_fraction"] = "dependent_fraction"
    source_column_key: str = Field(min_length=1, max_length=120)
    minimum_fraction: float = Field(ge=0, le=1)
    maximum_fraction: float = Field(ge=0, le=1)
    scale: int = Field(default=2, ge=0, le=12)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.minimum_fraction > self.maximum_fraction:
            raise ValueError("fraction minimum cannot exceed maximum")
        return self


GenerationRule = Annotated[
    SequenceRule
    | UniqueIdRule
    | CategoricalRule
    | IntegerRangeRule
    | DecimalRangeRule
    | DateRangeRule
    | BooleanRule
    | ConstantRule
    | DependentFractionRule,
    Field(discriminator="kind"),
]


class GenerationColumn(BaseModel):
    column_key: str = Field(min_length=1, max_length=120)
    rule: GenerationRule
    null_probability: float = Field(default=0, ge=0, le=1)

    model_config = ConfigDict(extra="forbid", frozen=True)


class GenerationComparisonConstraint(BaseModel):
    kind: Literal["comparison"] = "comparison"
    left_column_key: str = Field(min_length=1, max_length=120)
    operator: Literal[
        "less_than",
        "less_than_or_equal",
        "greater_than",
        "greater_than_or_equal",
    ]
    right_column_key: str = Field(min_length=1, max_length=120)

    model_config = ConfigDict(extra="forbid", frozen=True)


class GenerationUniqueConstraint(BaseModel):
    kind: Literal["unique"] = "unique"
    column_keys: tuple[str, ...] = Field(min_length=1, max_length=32)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("column_keys")
    @classmethod
    def require_unique_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("unique constraint column keys cannot repeat")
        return value


class GenerationNotNullConstraint(BaseModel):
    kind: Literal["not_null"] = "not_null"
    column_keys: tuple[str, ...] = Field(min_length=1, max_length=100)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("column_keys")
    @classmethod
    def require_unique_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("not-null constraint column keys cannot repeat")
        return value


GenerationConstraint = Annotated[
    GenerationComparisonConstraint
    | GenerationUniqueConstraint
    | GenerationNotNullConstraint,
    Field(discriminator="kind"),
]


class SyntheticDatasetSpec(BaseModel):
    dataset_name: str = Field(min_length=1, max_length=120)
    row_count: int = Field(ge=1)
    seed: int = Field(ge=0, le=2**63 - 1)
    generator_version: Literal[GENERATOR_VERSION] = GENERATOR_VERSION
    columns: tuple[GenerationColumn, ...] = Field(min_length=1, max_length=500)
    constraints: tuple[GenerationConstraint, ...] = Field(default=(), max_length=100)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        column_keys = tuple(column.column_key for column in self.columns)
        if len(column_keys) != len(set(column_keys)):
            raise ValueError("generation column keys must be unique")
        available: set[str] = set()
        for column in self.columns:
            if isinstance(column.rule, DependentFractionRule):
                if column.rule.source_column_key not in available:
                    raise ValueError(
                        "dependent generation rules must reference an earlier column"
                    )
            available.add(column.column_key)
        known = set(column_keys)
        for constraint in self.constraints:
            if isinstance(constraint, GenerationComparisonConstraint):
                if constraint.left_column_key == constraint.right_column_key:
                    raise ValueError(
                        "generation comparisons require two different columns"
                    )
                referenced = {
                    constraint.left_column_key,
                    constraint.right_column_key,
                }
            else:
                referenced = set(constraint.column_keys)
            if not referenced.issubset(known):
                raise ValueError("generation constraint references an unknown column")
        return self


__all__ = [
    "BooleanRule",
    "CategoricalRule",
    "ConstantRule",
    "DateRangeRule",
    "DecimalRangeRule",
    "DependentFractionRule",
    "GENERATOR_VERSION",
    "GenerationColumn",
    "GenerationComparisonConstraint",
    "GenerationConstraint",
    "GenerationNotNullConstraint",
    "GenerationRule",
    "GenerationUniqueConstraint",
    "IntegerRangeRule",
    "SequenceRule",
    "SyntheticDatasetSpec",
    "UniqueIdRule",
]
