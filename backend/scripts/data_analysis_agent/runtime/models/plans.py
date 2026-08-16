from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, ClassVar, Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from .canonical import canonical_content
from .datasets import DatasetSourceType
from .capabilities import CAPABILITY_PROFILE, CAPABILITY_PROFILE_VERSION
from .expressions import (
    Expression,
    expression_column_keys,
    validate_expression_size,
)
from .generation import SyntheticDatasetSpec
from .privacy import PrivacySummary
from .runs import AnalysisMode, StageTokenUsage, TokenUsage
from .workbook import a1_dimensions


PLAN_VERSION = "2.0"
LEGACY_PLAN_VERSION = "1.0"
# 2.1.0 documents the strict semantic-literal rules enforced by
# planning/type_system.py (currency and percentage never match a plain number).
PLANNER_PROMPT_VERSION = "2.1.0"
# 2.1.0 unifies the literal/compatibility rules onto planning/type_system.py and
# adds the Phase 9.1.3 selective early-approval reasons.
PLAN_VALIDATOR_VERSION = "2.1.0"
# 2.1.0 projects canonical content through the model schema instead of stripping
# display keys from dumped JSON, so opaque JsonValue payloads keep their keys.
PLAN_CANONICALIZER_VERSION = "2.1.0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,119}$")
_COLUMN_SUFFIX_RE = re.compile(r"^_[A-Za-z0-9_]{1,31}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PlanExecutor(str, Enum):
    NATIVE = "native"
    PYTHON = "python"
    FRONTEND = "frontend"


class PlanDataType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    DECIMAL = "decimal"
    CURRENCY = "currency"
    PERCENTAGE = "percentage"
    BOOLEAN = "boolean"
    DATE = "date"
    PERIOD = "period"
    UNKNOWN = "unknown"


class PlanColumn(BaseModel):
    # `label` is what the user reads; `key` is what the executor resolves.
    # See models/canonical.py for how this declaration reaches the plan hash.
    display_only_fields: ClassVar[frozenset[str]] = frozenset({"label"})

    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=240)
    data_type: PlanDataType
    unit: str | None = Field(default=None, max_length=100)
    nullable: bool = True

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if not _SYMBOL_RE.fullmatch(value):
            raise ValueError("column keys must be stable identifier symbols")
        return value


class PlanDatasetProvenance(BaseModel):
    source_dataset_id: str = Field(min_length=1, max_length=240)
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_type: DatasetSourceType
    artifact_id: str | None = Field(default=None, max_length=200)
    artifact_version_id: str | None = Field(default=None, max_length=200)
    document_id: str | None = Field(default=None, max_length=200)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    workbook_id: str | None = Field(default=None, max_length=200)
    workbook_revision: int | None = Field(default=None, ge=0)
    worksheet_id: str | None = Field(default=None, max_length=200)
    range_a1: str | None = Field(default=None, max_length=100)
    snapshot_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @model_validator(mode="after")
    def validate_locator(self) -> Self:
        workbook_identity = (
            self.workbook_id,
            self.workbook_revision,
            self.worksheet_id,
            self.snapshot_hash,
        )
        if any(value is not None for value in workbook_identity) and not all(
            value is not None
            for value in (*workbook_identity, self.range_a1)
        ):
            raise ValueError("workbook provenance must be complete")
        if (self.page_start is None) != (self.page_end is None):
            raise ValueError("PDF page bounds must be supplied together")
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("PDF page bounds are invalid")
        return self


class PlanInputDataset(BaseModel):
    display_only_fields: ClassVar[frozenset[str]] = frozenset({"title"})

    alias: str = Field(min_length=1, max_length=120)
    dataset_id: str = Field(min_length=1, max_length=240)
    dataset_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=240)
    row_count: int = Field(ge=0)
    columns: tuple[PlanColumn, ...] = Field(min_length=1, max_length=500)
    provenance: tuple[PlanDatasetProvenance, ...] = Field(
        min_length=1,
        max_length=20,
    )

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, value: str) -> str:
        if not _SYMBOL_RE.fullmatch(value):
            raise ValueError("dataset aliases must be identifier symbols")
        return value

    @model_validator(mode="after")
    def validate_columns(self) -> Self:
        keys = tuple(column.key for column in self.columns)
        if len(keys) != len(set(keys)):
            raise ValueError("input dataset column keys must be unique")
        return self


class PlanStepEstimate(BaseModel):
    rows_scanned: int = Field(default=0, ge=0)
    output_rows: int | None = Field(default=None, ge=0)
    cells_written: int = Field(default=0, ge=0)
    memory_mb: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0, ge=0)
    chart_cardinality: int = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class StepProvenance(BaseModel):
    display_only_fields: ClassVar[frozenset[str]] = frozenset({"description"})

    source_dataset_ids: tuple[str, ...] = Field(default=(), max_length=100)
    source_versions: tuple[str, ...] = Field(default=(), max_length=100)
    generated: bool = False
    description: str = Field(min_length=1, max_length=500)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        if len(self.source_dataset_ids) != len(self.source_versions):
            raise ValueError("provenance datasets and versions must align")
        return self


class PlanAssertionKind(str, Enum):
    SCHEMA_CONTAINS = "schema_contains"
    ROW_COUNT_AT_MOST = "row_count_at_most"
    NO_NULLS = "no_nulls"
    UNIQUE = "unique"
    VALUE_RANGE = "value_range"


class PlanAssertion(BaseModel):
    kind: PlanAssertionKind
    columns: tuple[str, ...] = Field(default=(), max_length=100)
    maximum_rows: int | None = Field(default=None, ge=0)
    minimum_value: float | None = None
    maximum_value: float | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_assertion(self) -> Self:
        if self.kind == PlanAssertionKind.ROW_COUNT_AT_MOST:
            if self.maximum_rows is None:
                raise ValueError("row-count assertions require maximum_rows")
        elif not self.columns:
            raise ValueError("column assertions require column keys")
        if (
            self.minimum_value is not None
            and self.maximum_value is not None
            and self.minimum_value > self.maximum_value
        ):
            raise ValueError("assertion minimum cannot exceed maximum")
        return self


class PlanStepBase(BaseModel):
    step_id: str = Field(min_length=1, max_length=120)
    depends_on: tuple[str, ...] = Field(default=(), max_length=64)
    executor: PlanExecutor
    output_alias: str = Field(min_length=1, max_length=120)
    expected_schema: tuple[PlanColumn, ...] = Field(
        min_length=1,
        max_length=500,
    )
    estimate: PlanStepEstimate = Field(default_factory=PlanStepEstimate)
    assertions: tuple[PlanAssertion, ...] = Field(default=(), max_length=24)
    provenance: StepProvenance = Field(
        default_factory=lambda: StepProvenance(
            description="Pending deterministic server canonicalization."
        )
    )
    python_reason: str | None = Field(default=None, min_length=8, max_length=500)
    network_access: Literal[False] = False

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @field_validator("step_id", "output_alias")
    @classmethod
    def validate_symbols(cls, value: str) -> str:
        if not _SYMBOL_RE.fullmatch(value):
            raise ValueError("step IDs and output aliases must be symbols")
        return value

    @field_validator("depends_on", mode="before")
    @classmethod
    def deduplicate_dependencies(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("depends_on must be a list or tuple")
        output = tuple(dict.fromkeys(str(item).strip() for item in value))
        if any(not _SYMBOL_RE.fullmatch(item) for item in output):
            raise ValueError("dependency IDs must be symbols")
        return output

    @model_validator(mode="after")
    def validate_base_step(self) -> Self:
        keys = tuple(column.key for column in self.expected_schema)
        if len(keys) != len(set(keys)):
            raise ValueError("expected output column keys must be unique")
        if self.executor == PlanExecutor.PYTHON and not self.python_reason:
            raise ValueError("Python steps require a declared reason")
        if self.executor != PlanExecutor.PYTHON and self.python_reason is not None:
            raise ValueError("python_reason is only valid for Python steps")
        return self


class PredicateValueType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    CURRENCY = "currency"
    PERCENTAGE = "percentage"


class ComparisonOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"


class ComparisonPredicate(BaseModel):
    kind: Literal["comparison"] = "comparison"
    column_key: str = Field(min_length=1, max_length=120)
    operator: ComparisonOperator
    value: JsonValue
    value_type: PredicateValueType
    unit: str | None = Field(default=None, max_length=100)

    model_config = ConfigDict(extra="forbid", frozen=True)


class SetPredicate(BaseModel):
    kind: Literal["set_membership"] = "set_membership"
    column_key: str = Field(min_length=1, max_length=120)
    operator: Literal["in", "not_in"]
    values: tuple[JsonValue, ...] = Field(min_length=1, max_length=100)
    value_type: PredicateValueType
    unit: str | None = Field(default=None, max_length=100)

    model_config = ConfigDict(extra="forbid", frozen=True)


class NullPredicate(BaseModel):
    kind: Literal["null_check"] = "null_check"
    column_key: str = Field(min_length=1, max_length=120)
    operator: Literal["is_null", "is_not_null"]

    model_config = ConfigDict(extra="forbid", frozen=True)


RowPredicate = Annotated[
    ComparisonPredicate | SetPredicate | NullPredicate,
    Field(discriminator="kind"),
]


class LegacyGenerateDatasetStep(PlanStepBase):
    kind: Literal["generate_dataset"] = "generate_dataset"
    row_count: int = Field(ge=1)
    generation_instructions: str = Field(min_length=1, max_length=2_000)
    random_seed: int | None = Field(default=None, ge=0)


class LegacyFilterRowsStep(PlanStepBase):
    kind: Literal["filter_rows"] = "filter_rows"
    input_alias: str = Field(min_length=1, max_length=120)
    predicates: tuple[RowPredicate, ...] = Field(min_length=1, max_length=24)
    combine_with: Literal["and", "or"] = "and"


class GenerateDatasetStep(PlanStepBase):
    kind: Literal["generate_dataset"] = "generate_dataset"
    generation: SyntheticDatasetSpec

    @property
    def row_count(self) -> int:
        return self.generation.row_count

    @property
    def random_seed(self) -> int:
        return self.generation.seed

    @model_validator(mode="after")
    def validate_generation_schema(self) -> Self:
        generated_keys = tuple(column.column_key for column in self.generation.columns)
        expected_keys = tuple(column.key for column in self.expected_schema)
        if generated_keys != expected_keys:
            raise ValueError(
                "generation columns must exactly match expected_schema order"
            )
        return self


class FilterRowsStep(PlanStepBase):
    kind: Literal["filter_rows"] = "filter_rows"
    input_alias: str = Field(min_length=1, max_length=120)
    predicate: Expression
    null_predicate_policy: Literal["exclude", "include", "error"] = "exclude"

    @model_validator(mode="after")
    def validate_predicate_size(self) -> Self:
        validate_expression_size(self.predicate)
        return self


class SortKey(BaseModel):
    column_key: str = Field(min_length=1, max_length=120)
    direction: Literal["ascending", "descending"] = "ascending"
    nulls: Literal["first", "last"] = "last"

    model_config = ConfigDict(extra="forbid", frozen=True)


class SortRowsStep(PlanStepBase):
    kind: Literal["sort_rows"] = "sort_rows"
    input_alias: str = Field(min_length=1, max_length=120)
    keys: tuple[SortKey, ...] = Field(min_length=1, max_length=32)
    stable: Literal[True] = True


class SelectColumnsStep(PlanStepBase):
    kind: Literal["select_columns"] = "select_columns"
    input_alias: str = Field(min_length=1, max_length=120)
    column_keys: tuple[str, ...] = Field(min_length=1, max_length=500)


class ColumnRename(BaseModel):
    display_only_fields: ClassVar[frozenset[str]] = frozenset({"output_label"})

    source_key: str = Field(min_length=1, max_length=120)
    output_key: str = Field(min_length=1, max_length=120)
    output_label: str = Field(min_length=1, max_length=240)

    model_config = ConfigDict(extra="forbid", frozen=True)


class RenameColumnsStep(PlanStepBase):
    kind: Literal["rename_columns"] = "rename_columns"
    input_alias: str = Field(min_length=1, max_length=120)
    renames: tuple[ColumnRename, ...] = Field(min_length=1, max_length=500)


class FillRule(BaseModel):
    column_key: str = Field(min_length=1, max_length=120)
    strategy: Literal[
        "constant",
        "mean",
        "median",
        "mode",
        "forward_fill",
        "backward_fill",
    ]
    value: JsonValue | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        if self.strategy == "constant" and self.value is None:
            raise ValueError("constant fill requires a value")
        if self.strategy != "constant" and self.value is not None:
            raise ValueError("only constant fill accepts a value")
        return self


class LegacyFillMissingStep(PlanStepBase):
    """Phase 8 fill contract retained only for persisted plan history."""

    kind: Literal["fill_missing"] = "fill_missing"
    input_alias: str = Field(min_length=1, max_length=120)
    rules: tuple[FillRule, ...] = Field(min_length=1, max_length=100)


class FillMissingStep(PlanStepBase):
    kind: Literal["fill_missing"] = "fill_missing"
    input_alias: str = Field(min_length=1, max_length=120)
    rules: tuple[FillRule, ...] = Field(min_length=1, max_length=100)
    group_by: tuple[str, ...] = Field(default=(), max_length=100)
    order_by: tuple[SortKey, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def require_order_for_directional_fill(self) -> Self:
        if any(
            rule.strategy in {"forward_fill", "backward_fill"}
            for rule in self.rules
        ) and not self.order_by:
            raise ValueError("directional fill requires deterministic order_by keys")
        return self


class DeduplicateStep(PlanStepBase):
    kind: Literal["deduplicate"] = "deduplicate"
    input_alias: str = Field(min_length=1, max_length=120)
    key_columns: tuple[str, ...] = Field(min_length=1, max_length=100)
    keep: Literal["first", "last", "error"] = "first"
    order_by: tuple[SortKey, ...] = Field(default=(), max_length=32)
    order_policy: Literal["stable_input", "sort_keys"] = "stable_input"

    @model_validator(mode="after")
    def validate_order_policy(self) -> Self:
        if (self.order_policy == "sort_keys") != bool(self.order_by):
            raise ValueError("sort_keys deduplication requires order_by and vice versa")
        return self


class LegacyDeriveColumnStep(PlanStepBase):
    kind: Literal["derive_column"] = "derive_column"
    input_alias: str = Field(min_length=1, max_length=120)
    output_column: PlanColumn
    expression: str = Field(min_length=1, max_length=2_000)
    # Constant/source-label columns legitimately read no input columns.
    referenced_columns: tuple[str, ...] = Field(default=(), max_length=100)
    expression_language: Literal["native", "python", "spreadsheet_formula"]


class DeriveColumnStep(PlanStepBase):
    kind: Literal["derive_column"] = "derive_column"
    input_alias: str = Field(min_length=1, max_length=120)
    output_column: PlanColumn
    expression: Expression
    rounding_scale: int | None = Field(default=None, ge=0, le=12)
    rounding_mode: Literal["half_even", "half_up", "floor", "ceiling"] = "half_even"
    overflow_policy: Literal["null", "error"] = "error"

    @property
    def referenced_columns(self) -> tuple[str, ...]:
        return expression_column_keys(self.expression)

    @model_validator(mode="after")
    def validate_expression_contract(self) -> Self:
        validate_expression_size(self.expression)
        if self.rounding_scale is not None and self.output_column.data_type not in {
            PlanDataType.NUMBER,
            PlanDataType.DECIMAL,
            PlanDataType.CURRENCY,
            PlanDataType.PERCENTAGE,
        }:
            raise ValueError("rounding is only valid for numeric output columns")
        return self


class AggregateMetric(BaseModel):
    input_column_key: str = Field(min_length=1, max_length=120)
    function: Literal[
        "sum",
        "mean",
        "median",
        "min",
        "max",
        "count",
        "count_distinct",
        "standard_deviation",
    ]
    output_column: PlanColumn
    null_policy: Literal["ignore", "include", "error"] = "ignore"
    rounding_scale: int | None = Field(default=None, ge=0, le=12)
    rounding_mode: Literal["half_even", "half_up", "floor", "ceiling"] = "half_even"

    model_config = ConfigDict(extra="forbid", frozen=True)


class AggregateStep(PlanStepBase):
    kind: Literal["aggregate"] = "aggregate"
    input_alias: str = Field(min_length=1, max_length=120)
    group_by: tuple[str, ...] = Field(default=(), max_length=100)
    metrics: tuple[AggregateMetric, ...] = Field(min_length=1, max_length=100)


class JoinKeyPair(BaseModel):
    left_column_key: str = Field(min_length=1, max_length=120)
    right_column_key: str = Field(min_length=1, max_length=120)

    model_config = ConfigDict(extra="forbid", frozen=True)


class LegacyJoinStep(PlanStepBase):
    """Phase 8 join contract retained only for persisted plan history."""

    kind: Literal["join"] = "join"
    left_alias: str = Field(min_length=1, max_length=120)
    right_alias: str = Field(min_length=1, max_length=120)
    join_type: Literal["inner", "left", "right", "full"]
    keys: tuple[JoinKeyPair, ...] = Field(min_length=1, max_length=16)


class JoinStep(PlanStepBase):
    kind: Literal["join"] = "join"
    left_alias: str = Field(min_length=1, max_length=120)
    right_alias: str = Field(min_length=1, max_length=120)
    join_type: Literal["inner", "left", "right", "full"]
    keys: tuple[JoinKeyPair, ...] = Field(min_length=1, max_length=16)
    expected_cardinality: Literal[
        "one_to_one",
        "one_to_many",
        "many_to_one",
        "many_to_many",
    ]
    nulls_match: Literal[False] = False
    left_suffix: str = Field(default="_left", min_length=1, max_length=32)
    right_suffix: str = Field(default="_right", min_length=1, max_length=32)
    maximum_expansion_ratio: float = Field(default=10, ge=1, le=1_000)

    @model_validator(mode="after")
    def validate_suffixes(self) -> Self:
        if self.left_suffix == self.right_suffix:
            raise ValueError("join suffixes must be different")
        if not all(
            _COLUMN_SUFFIX_RE.fullmatch(suffix)
            for suffix in (self.left_suffix, self.right_suffix)
        ):
            raise ValueError("join suffixes must be identifier-safe")
        left_keys = tuple(pair.left_column_key for pair in self.keys)
        right_keys = tuple(pair.right_column_key for pair in self.keys)
        if (
            len(left_keys) != len(set(left_keys))
            or len(right_keys) != len(set(right_keys))
        ):
            raise ValueError("join key columns cannot repeat")
        return self


class PivotCategoryPolicy(BaseModel):
    mode: Literal["explicit", "discover"]
    values: tuple[JsonValue, ...] = Field(default=(), max_length=500)
    maximum_categories: int = Field(default=100, ge=1, le=500)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if self.mode == "explicit" and not self.values:
            raise ValueError("explicit pivot categories require values")
        if self.mode == "discover" and self.values:
            raise ValueError("discovered pivot categories cannot include values")
        if len(self.values) > self.maximum_categories:
            raise ValueError("pivot categories exceed maximum_categories")
        canonical_values = tuple(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            for value in self.values
        )
        if len(canonical_values) != len(set(canonical_values)):
            raise ValueError("pivot categories must be unique")
        return self


class LegacyPivotStep(PlanStepBase):
    """Phase 8 pivot contract retained only for persisted plan history."""

    kind: Literal["pivot"] = "pivot"
    input_alias: str = Field(min_length=1, max_length=120)
    index_columns: tuple[str, ...] = Field(min_length=1, max_length=100)
    pivot_column: str = Field(min_length=1, max_length=120)
    value_column: str = Field(min_length=1, max_length=120)
    aggregation: Literal["sum", "mean", "min", "max", "count"]


class PivotStep(PlanStepBase):
    kind: Literal["pivot"] = "pivot"
    input_alias: str = Field(min_length=1, max_length=120)
    index_columns: tuple[str, ...] = Field(min_length=1, max_length=100)
    pivot_column: str = Field(min_length=1, max_length=120)
    value_column: str = Field(min_length=1, max_length=120)
    aggregation: Literal["sum", "mean", "min", "max", "count"]
    category_policy: PivotCategoryPolicy
    maximum_output_columns: int = Field(default=500, ge=1, le=500)

    @model_validator(mode="after")
    def validate_pivot_width(self) -> Self:
        if len(self.index_columns) != len(set(self.index_columns)):
            raise ValueError("pivot index columns cannot repeat")
        if (
            self.pivot_column in self.index_columns
            or self.value_column in self.index_columns
            or self.pivot_column == self.value_column
        ):
            raise ValueError("pivot index, category, and value columns must differ")
        category_width = (
            len(self.category_policy.values)
            if self.category_policy.mode == "explicit"
            else self.category_policy.maximum_categories
        )
        if len(self.index_columns) + category_width > self.maximum_output_columns:
            raise ValueError("pivot category policy exceeds maximum_output_columns")
        return self


class UnpivotStep(PlanStepBase):
    kind: Literal["unpivot"] = "unpivot"
    input_alias: str = Field(min_length=1, max_length=120)
    id_columns: tuple[str, ...] = Field(default=(), max_length=100)
    value_columns: tuple[str, ...] = Field(min_length=1, max_length=400)
    variable_column: PlanColumn
    value_column: PlanColumn


class StatisticalTestStep(PlanStepBase):
    kind: Literal["statistical_test"] = "statistical_test"
    input_alias: str = Field(min_length=1, max_length=120)
    test: Literal[
        "t_test",
        "chi_square",
        "anova",
        "correlation",
        "mann_whitney",
    ]
    feature_columns: tuple[str, ...] = Field(min_length=1, max_length=100)
    significance_level: float = Field(default=0.05, gt=0, lt=1)


class TrainModelStep(PlanStepBase):
    kind: Literal["train_model"] = "train_model"
    # Training produces a serialized model/evaluation artifact rather than a
    # row set. Features and target remain strictly typed against the input.
    expected_schema: tuple[PlanColumn, ...] = Field(default=(), max_length=500)
    input_alias: str = Field(min_length=1, max_length=120)
    model_type: Literal[
        "linear_regression",
        "logistic_regression",
        "decision_tree",
        "random_forest",
        "knn",
        "kmeans",
    ]
    feature_columns: tuple[str, ...] = Field(min_length=1, max_length=200)
    target_column: str | None = Field(default=None, max_length=120)
    validation_strategy: Literal["holdout", "cross_validation"] = "holdout"


class VisualizationStep(PlanStepBase):
    display_only_fields: ClassVar[frozenset[str]] = frozenset({"title"})

    kind: Literal["generate_visualization"] = "generate_visualization"
    # A visualization produces a chart artifact, not a tabular dataset. Its
    # source columns are declared by x/y/group fields and validated against the
    # input alias, so an empty output schema is both precise and intentional.
    expected_schema: tuple[PlanColumn, ...] = Field(default=(), max_length=500)
    input_alias: str = Field(min_length=1, max_length=120)
    chart_type: Literal[
        "bar",
        "line",
        "area",
        "scatter",
        "histogram",
        "box",
        "heatmap",
        "confusion_matrix",
        "knn_decision_boundary",
        "cluster_plot",
        "correlation_matrix",
    ]
    x_column: str | None = Field(default=None, max_length=120)
    y_columns: tuple[str, ...] = Field(default=(), max_length=20)
    group_column: str | None = Field(default=None, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    attach_to_workbook: bool = False


class ComposeResponseStep(PlanStepBase):
    kind: Literal["compose_response"] = "compose_response"
    # A composed response is a text artifact and therefore has no column schema.
    expected_schema: tuple[PlanColumn, ...] = Field(default=(), max_length=500)
    # Empty is accepted at the untrusted boundary and deterministically expands
    # to the current prepared inputs before validation.
    input_aliases: tuple[str, ...] = Field(default=(), max_length=32)
    response_format: Literal["text", "markdown", "structured"] = "markdown"
    include_provenance: Literal[True] = True

    @model_validator(mode="after")
    def require_artifact_output(self) -> Self:
        if self.expected_schema:
            raise ValueError("compose_response must not declare a tabular schema")
        return self


PlanStep = Annotated[
    GenerateDatasetStep
    | FilterRowsStep
    | SortRowsStep
    | SelectColumnsStep
    | RenameColumnsStep
    | FillMissingStep
    | DeduplicateStep
    | DeriveColumnStep
    | AggregateStep
    | JoinStep
    | PivotStep
    | UnpivotStep
    | StatisticalTestStep
    | TrainModelStep
    | VisualizationStep
    | ComposeResponseStep,
    Field(discriminator="kind"),
]

LEGACY_PLAN_STEP_TYPES = (
    LegacyGenerateDatasetStep,
    LegacyFilterRowsStep,
    LegacyFillMissingStep,
    LegacyDeriveColumnStep,
    LegacyJoinStep,
    LegacyPivotStep,
)

# Persisted v1 records only. Every member forbids extra fields and every legacy
# variant carries a field its v2 counterpart rejects, so this union resolves
# deterministically even though `kind` alone cannot discriminate it.
HistoricalPlanStep = (
    LegacyGenerateDatasetStep
    | LegacyFilterRowsStep
    | LegacyFillMissingStep
    | LegacyDeriveColumnStep
    | LegacyJoinStep
    | LegacyPivotStep
    | PlanStep
)


def step_input_aliases(step: HistoricalPlanStep) -> tuple[str, ...]:
    """Return the dataset aliases a step reads, for v2 and persisted v1 steps."""

    if isinstance(step, (GenerateDatasetStep, LegacyGenerateDatasetStep)):
        return ()
    if isinstance(step, (JoinStep, LegacyJoinStep)):
        return (step.left_alias, step.right_alias)
    if isinstance(step, ComposeResponseStep):
        return step.input_aliases
    return (step.input_alias,)


def join_output_schema(
    step: JoinStep,
    left: tuple[PlanColumn, ...],
    right: tuple[PlanColumn, ...],
) -> tuple[PlanColumn, ...]:
    """Derive the versioned join collision/coalescing schema deterministically."""

    left_keys = {column.key for column in left}
    right_keys = {column.key for column in right}
    collisions = left_keys.intersection(right_keys)
    coalesced_keys = {
        pair.left_column_key
        for pair in step.keys
        if pair.left_column_key == pair.right_column_key
    }
    renamed_collisions = collisions.difference(coalesced_keys)
    left_output = tuple(
        column.model_copy(
            update={"key": f"{column.key}{step.left_suffix}"}
        )
        if column.key in renamed_collisions
        else column
        for column in left
    )
    right_output = tuple(
        column.model_copy(
            update={"key": f"{column.key}{step.right_suffix}"}
        )
        if column.key in renamed_collisions
        else column
        for column in right
        if column.key not in coalesced_keys
    )
    return (*left_output, *right_output)


class WorkbookPlacementPolicy(str, Enum):
    ADJACENT_RIGHT = "adjacent_right"
    NEW_SHEET = "new_sheet"
    EXACT_RANGE = "exact_range"


class WorkbookCollisionPolicy(str, Enum):
    FAIL = "fail"
    CREATE_NEW_SHEET = "create_new_sheet"
    REQUIRE_REAPPROVAL = "require_reapproval"


class WorkbookWriteTarget(BaseModel):
    workbook_id: str = Field(min_length=1, max_length=200)
    worksheet_id: str = Field(min_length=1, max_length=200)
    base_workbook_revision: int = Field(ge=0)
    base_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_range_a1: str = Field(min_length=5, max_length=100)
    placement_policy: WorkbookPlacementPolicy
    exact_target_range_a1: str | None = Field(default=None, max_length=100)
    minimum_column_gap: int = Field(default=2, ge=0, le=100)
    collision_policy: WorkbookCollisionPolicy = (
        WorkbookCollisionPolicy.REQUIRE_REAPPROVAL
    )

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @field_validator("source_range_a1", "exact_target_range_a1", mode="before")
    @classmethod
    def validate_bounded_ranges(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        # Parse at the untrusted proposal boundary so malformed LLM output is
        # handled by the existing single repair loop instead of crashing the
        # deterministic overlap validator.
        a1_dimensions(normalized)
        return normalized

    @model_validator(mode="after")
    def validate_placement(self) -> Self:
        exact = self.placement_policy == WorkbookPlacementPolicy.EXACT_RANGE
        if exact != (self.exact_target_range_a1 is not None):
            raise ValueError(
                "exact-range placement and exact_target_range_a1 must agree"
            )
        return self


class WorkbookWriteIntent(BaseModel):
    kind: Literal["write_workbook"] = "write_workbook"
    intent_id: str = Field(min_length=1, max_length=120)
    input_alias: str = Field(min_length=1, max_length=120)
    target: WorkbookWriteTarget
    destructive: bool = False
    overwrite_formulas: bool = False
    requires_final_approval: Literal[True] = True

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class ArtifactWriteIntent(BaseModel):
    kind: Literal["create_artifact"] = "create_artifact"
    intent_id: str = Field(min_length=1, max_length=120)
    input_alias: str = Field(min_length=1, max_length=120)
    artifact_kind: Literal["dataset", "chart", "report", "model"]
    filename_hint: str = Field(min_length=1, max_length=255)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


PlanWriteIntent = Annotated[
    WorkbookWriteIntent | ArtifactWriteIntent,
    Field(discriminator="kind"),
]


class ExpectedArtifact(BaseModel):
    display_only_fields: ClassVar[frozenset[str]] = frozenset({"title"})

    alias: str = Field(min_length=1, max_length=120)
    kind: Literal["dataset", "chart", "text", "workbook_patch", "model"]
    source_alias: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class PlanProposal(BaseModel):
    """Untrusted structured LLM output; identities are injected server-side."""

    intent: str = Field(min_length=1, max_length=2_000)
    assumptions: tuple[str, ...] = Field(default=(), max_length=24)
    steps: tuple[PlanStep, ...] = Field(min_length=1, max_length=64)
    write_intents: tuple[PlanWriteIntent, ...] = Field(default=(), max_length=24)
    expected_artifacts: tuple[ExpectedArtifact, ...] = Field(
        default=(),
        max_length=32,
    )

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class AnalysisPlanDraft(PlanProposal):
    plan_version: Literal[PLAN_VERSION] = PLAN_VERSION
    capability_profile: Literal[CAPABILITY_PROFILE] = CAPABILITY_PROFILE
    capability_version: Literal[CAPABILITY_PROFILE_VERSION] = CAPABILITY_PROFILE_VERSION
    run_id: str = Field(min_length=36, max_length=36)
    mode: AnalysisMode
    input_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_datasets: tuple[PlanInputDataset, ...] = Field(
        min_length=1,
        max_length=30,
    )

    @field_validator("run_id", mode="before")
    @classmethod
    def validate_run_id(cls, value: object) -> str:
        try:
            return str(UUID(str(value or "").strip()))
        except (ValueError, AttributeError) as exc:
            raise ValueError("run_id must be a UUID") from exc

    @model_validator(mode="after")
    def validate_identities(self) -> Self:
        aliases = tuple(dataset.alias for dataset in self.input_datasets)
        if len(aliases) != len(set(aliases)):
            raise ValueError("input dataset aliases must be unique")
        dataset_ids = tuple(dataset.dataset_id for dataset in self.input_datasets)
        if len(dataset_ids) != len(set(dataset_ids)):
            raise ValueError("input dataset IDs must be unique")
        step_ids = tuple(step.step_id for step in self.steps)
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step IDs must be unique")
        output_aliases = tuple(step.output_alias for step in self.steps)
        if len(output_aliases) != len(set(output_aliases)):
            raise ValueError("step output aliases must be unique")
        if set(aliases).intersection(output_aliases):
            raise ValueError("step outputs cannot shadow input aliases")
        intent_ids = tuple(intent.intent_id for intent in self.write_intents)
        if len(intent_ids) != len(set(intent_ids)):
            raise ValueError("write intent IDs must be unique")
        return self


class ApprovalReason(str, Enum):
    EXPENSIVE_PYTHON = "expensive_python"
    LARGE_GENERATED_DATASET = "large_generated_dataset"
    LONG_RUNNING = "long_running"
    MEANINGFUL_COST = "meaningful_cost"
    DESTRUCTIVE_WRITE = "destructive_write"
    FORMULA_OVERWRITE = "formula_overwrite"
    # Phase 9.1.3: a safe intent should not be approved twice, but a plan that
    # rewrites a large region, or that rests on assumptions the planner had to
    # invent, is worth one explicit confirmation before it runs.
    BROAD_IMPACT = "broad_impact"
    AMBIGUOUS_REQUEST = "ambiguous_request"


class ApprovalPolicy(BaseModel):
    plan_approval_required: bool
    plan_approval_reasons: tuple[ApprovalReason, ...] = Field(
        default=(),
        max_length=12,
    )
    final_patch_approval_required: bool
    auto_execute_read_only: bool

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_reasons(self) -> Self:
        if self.plan_approval_required != bool(self.plan_approval_reasons):
            raise ValueError("plan approval reasons must match the policy")
        return self


class PlanApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PlanRejectionReason(str, Enum):
    WRONG_DATASET = "wrong_dataset"
    WRONG_OPERATION = "wrong_operation"
    WRONG_TARGET = "wrong_target"
    TOO_DESTRUCTIVE = "too_destructive"
    OTHER = "other"


class PlanApprovalRecord(BaseModel):
    status: PlanApprovalStatus
    actor_user_id: str | None = Field(default=None, max_length=200)
    comment: str | None = Field(default=None, max_length=1_000)
    rejection_reason: PlanRejectionReason | None = None
    requested_at: datetime | None = None
    decided_at: datetime | None = None
    decision_id: str | None = Field(default=None, max_length=36)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        decided = self.status in {
            PlanApprovalStatus.APPROVED,
            PlanApprovalStatus.REJECTED,
        }
        if decided != all(
            value is not None
            for value in (
                self.actor_user_id,
                self.decided_at,
                self.decision_id,
            )
        ):
            raise ValueError("approval decision metadata is incomplete")
        if self.status == PlanApprovalStatus.PENDING and self.requested_at is None:
            raise ValueError("pending approval requires requested_at")
        if self.status in {
            PlanApprovalStatus.APPROVED,
            PlanApprovalStatus.REJECTED,
        }:
            if self.requested_at is None:
                raise ValueError("approval decisions must retain requested_at")
            if self.decided_at is not None and self.decided_at < self.requested_at:
                raise ValueError("approval cannot precede its request")
        if self.status == PlanApprovalStatus.NOT_REQUIRED and any(
            value is not None
            for value in (
                self.actor_user_id,
                self.requested_at,
                self.decided_at,
                self.decision_id,
                self.comment,
                self.rejection_reason,
            )
        ):
            raise ValueError("not-required approval cannot contain a decision")
        if (
            self.rejection_reason is not None
            and self.status != PlanApprovalStatus.REJECTED
        ):
            raise ValueError("only rejected plans may include a rejection reason")
        return self


class AnalysisPlanStatus(str, Enum):
    READY = "ready"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class PlanDiagnostics(BaseModel):
    generation_attempt: int = Field(ge=1, le=2)
    repair_count: int = Field(ge=0, le=1)
    validation_warning_count: int = Field(default=0, ge=0)
    validation_error_count: int = Field(default=0, ge=0)
    reused_persisted_plan: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisPlan(AnalysisPlanDraft):
    """Validated immutable content plus mutable approval lifecycle metadata."""

    # Persisted v1 records remain readable for audit/history. They are rejected
    # by execution admission and can never be produced by PlanProposal.
    plan_version: Literal[LEGACY_PLAN_VERSION, PLAN_VERSION] = PLAN_VERSION
    capability_profile: str = CAPABILITY_PROFILE
    capability_version: str = CAPABILITY_PROFILE_VERSION
    steps: tuple[HistoricalPlanStep, ...] = Field(min_length=1, max_length=64)
    plan_id: str = Field(min_length=36, max_length=36)
    user_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    status: AnalysisPlanStatus
    approval_policy: ApprovalPolicy
    approval: PlanApprovalRecord
    diagnostics: PlanDiagnostics
    model: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=100)
    validator_version: str = Field(
        default=PLAN_VALIDATOR_VERSION,
        min_length=1,
        max_length=100,
    )
    canonicalizer_version: str = Field(
        default=PLAN_CANONICALIZER_VERSION,
        min_length=1,
        max_length=100,
    )
    privacy: PrivacySummary = Field(default_factory=PrivacySummary)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    write_target_keys: tuple[str, ...] = Field(default=(), max_length=24)
    reservation_active: bool = False
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    token_usage_by_stage: dict[str, StageTokenUsage] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="before")
    @classmethod
    def preserve_legacy_step_contracts(cls, value: object) -> object:
        if not isinstance(value, Mapping) or value.get("plan_version") != "1.0":
            return value
        output = dict(value)
        restored_steps: list[object] = []
        for step in output.get("steps", ()):
            if not isinstance(step, Mapping):
                restored_steps.append(step)
                continue
            kind = step.get("kind")
            if kind == "generate_dataset" and "generation_instructions" in step:
                restored_steps.append(LegacyGenerateDatasetStep.model_validate(step))
            elif kind == "filter_rows" and "predicates" in step:
                restored_steps.append(LegacyFilterRowsStep.model_validate(step))
            elif kind == "derive_column" and "expression_language" in step:
                restored_steps.append(LegacyDeriveColumnStep.model_validate(step))
            elif kind == "fill_missing" and "order_by" not in step:
                restored_steps.append(LegacyFillMissingStep.model_validate(step))
            elif kind == "join" and "expected_cardinality" not in step:
                restored_steps.append(LegacyJoinStep.model_validate(step))
            elif kind == "pivot" and "category_policy" not in step:
                restored_steps.append(LegacyPivotStep.model_validate(step))
            else:
                restored_steps.append(step)
        output["steps"] = tuple(restored_steps)
        return output

    @field_validator("plan_id", mode="before")
    @classmethod
    def validate_plan_id(cls, value: object) -> str:
        try:
            return str(UUID(str(value or "").strip()))
        except (ValueError, AttributeError) as exc:
            raise ValueError("plan_id must be a UUID") from exc

    @model_validator(mode="after")
    def validate_plan_lifecycle(self) -> Self:
        expected_targets = workbook_write_target_keys(self.write_intents)
        if self.write_target_keys != expected_targets:
            raise ValueError("write_target_keys must match workbook intents")
        if self.reservation_active and not self.write_target_keys:
            raise ValueError("only workbook plans can reserve write targets")
        if self.status in {
            AnalysisPlanStatus.REJECTED,
            AnalysisPlanStatus.SUPERSEDED,
        } and self.reservation_active:
            raise ValueError("rejected or superseded plans cannot reserve targets")
        expected_status = {
            PlanApprovalStatus.NOT_REQUIRED: AnalysisPlanStatus.READY,
            PlanApprovalStatus.PENDING: AnalysisPlanStatus.AWAITING_PLAN_APPROVAL,
            PlanApprovalStatus.APPROVED: AnalysisPlanStatus.APPROVED,
            PlanApprovalStatus.REJECTED: AnalysisPlanStatus.REJECTED,
        }[self.approval.status]
        if self.status not in {expected_status, AnalysisPlanStatus.SUPERSEDED}:
            raise ValueError("plan status and approval status disagree")
        if self.updated_at < self.created_at:
            raise ValueError("plan updated_at cannot precede created_at")
        # Only a plan produced by the active canonicalizer can be re-derived.
        # Records written by an earlier canonicalizer stay readable for audit;
        # execution admission rejects them (runtime/execution/admission.py).
        if (
            self.plan_version == PLAN_VERSION
            and self.canonicalizer_version == PLAN_CANONICALIZER_VERSION
            and self.plan_hash != analysis_plan_hash(self)
        ):
            raise ValueError("plan_hash does not match canonical plan content")
        return self


class WorkbookVersionGuard(BaseModel):
    workbook_id: str = Field(min_length=1, max_length=200)
    worksheet_id: str = Field(min_length=1, max_length=200)
    workbook_revision: int = Field(ge=0)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @property
    def target_key(self) -> str:
        return f"{self.workbook_id}:{self.worksheet_id}"


class PlanApprovalCommand(BaseModel):
    decision: Literal["approve", "reject"]
    plan_id: str = Field(min_length=36, max_length=36)
    expected_revision: int = Field(ge=1)
    expected_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_input_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    workbook_guards: tuple[WorkbookVersionGuard, ...] = Field(
        default=(),
        max_length=24,
    )
    comment: str | None = Field(default=None, max_length=1_000)
    rejection_reason: PlanRejectionReason | None = None
    decision_id: str = Field(min_length=36, max_length=36)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @field_validator("plan_id", "decision_id", mode="before")
    @classmethod
    def validate_uuids(cls, value: object) -> str:
        try:
            return str(UUID(str(value or "").strip()))
        except (ValueError, AttributeError) as exc:
            raise ValueError("approval IDs must be UUIDs") from exc

    @model_validator(mode="after")
    def validate_rejection_reason(self) -> Self:
        if self.decision == "approve" and self.rejection_reason is not None:
            raise ValueError("approved plans cannot include a rejection reason")
        return self


class PatchImpactSummary(BaseModel):
    rows_added: int = Field(default=0, ge=0)
    rows_removed: int = Field(default=0, ge=0)
    cells_written: int = Field(default=0, ge=0)
    formulas_added: int = Field(default=0, ge=0)
    formulas_replaced: int = Field(default=0, ge=0)
    sheets_created: int = Field(default=0, ge=0)
    charts_attached: int = Field(default=0, ge=0)
    destructive: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)


class FinalPatchProposal(BaseModel):
    """Phase 9 will create these; Phase 8 defines the approval boundary."""

    patch_id: str = Field(min_length=36, max_length=36)
    run_id: str = Field(min_length=36, max_length=36)
    user_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(min_length=36, max_length=36)
    plan_revision: int = Field(ge=1)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch_artifact_version_id: str = Field(min_length=1, max_length=200)
    workbook_guards: tuple[WorkbookVersionGuard, ...] = Field(
        min_length=1,
        max_length=24,
    )
    impact: PatchImpactSummary
    approval: PlanApprovalRecord
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @field_validator("patch_id", "run_id", "plan_id", mode="before")
    @classmethod
    def validate_uuids(cls, value: object) -> str:
        try:
            return str(UUID(str(value or "").strip()))
        except (ValueError, AttributeError) as exc:
            raise ValueError("patch and plan references must be UUIDs") from exc

    @model_validator(mode="after")
    def validate_pending_approval(self) -> Self:
        if self.approval.status == PlanApprovalStatus.NOT_REQUIRED:
            raise ValueError("workbook patches always require final approval")
        if self.updated_at < self.created_at:
            raise ValueError("patch updated_at cannot precede created_at")
        return self


class FinalPatchApprovalCommand(BaseModel):
    decision: Literal["approve", "reject"]
    patch_id: str = Field(min_length=36, max_length=36)
    expected_patch_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    workbook_guards: tuple[WorkbookVersionGuard, ...] = Field(
        min_length=1,
        max_length=24,
    )
    comment: str | None = Field(default=None, max_length=1_000)
    decision_id: str = Field(min_length=36, max_length=36)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @field_validator("patch_id", "decision_id", mode="before")
    @classmethod
    def validate_uuids(cls, value: object) -> str:
        try:
            return str(UUID(str(value or "").strip()))
        except (ValueError, AttributeError) as exc:
            raise ValueError("patch approval IDs must be UUIDs") from exc


def compute_input_signature(datasets: tuple[PlanInputDataset, ...]) -> str:
    payload = [
        canonical_content(dataset)
        for dataset in sorted(datasets, key=lambda item: item.dataset_id)
    ]
    return _sha256_json(payload)


def workbook_write_target_keys(
    intents: tuple[PlanWriteIntent, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            f"{intent.target.workbook_id}:{intent.target.worksheet_id}"
            for intent in intents
            if isinstance(intent, WorkbookWriteIntent)
        )
    )


def analysis_plan_hash(plan: AnalysisPlan) -> str:
    """Hash immutable semantics, excluding lifecycle and transient diagnostics."""

    return _canonical_plan_content_hash(
        draft=plan,
        user_id=plan.user_id,
        workspace_id=plan.workspace_id,
        revision=plan.revision,
        approval_policy=plan.approval_policy,
        model=plan.model,
        prompt_version=plan.prompt_version,
        validator_version=plan.validator_version,
        canonicalizer_version=plan.canonicalizer_version,
        privacy=plan.privacy,
    )


def _canonical_plan_content_hash(
    *,
    draft: AnalysisPlanDraft,
    user_id: str,
    workspace_id: str,
    revision: int,
    approval_policy: ApprovalPolicy,
    model: str,
    prompt_version: str,
    validator_version: str,
    canonicalizer_version: str,
    privacy: PrivacySummary,
) -> str:
    # `intent` and `assumptions` are the user-facing narrative. They are omitted
    # here so an identical recipe stays cache- and approval-identical no matter
    # how the planner phrased it; the approval record binds the prose separately.
    payload = {
        "plan_version": draft.plan_version,
        "capability_profile": draft.capability_profile,
        "capability_version": draft.capability_version,
        "run_id": draft.run_id,
        "user_id": user_id,
        "workspace_id": workspace_id,
        "revision": revision,
        "mode": draft.mode.value,
        "input_signature": draft.input_signature,
        "input_datasets": [canonical_content(item) for item in draft.input_datasets],
        "steps": [canonical_content(item) for item in draft.steps],
        "write_intents": [canonical_content(item) for item in draft.write_intents],
        "expected_artifacts": [
            canonical_content(item) for item in draft.expected_artifacts
        ],
        "approval_policy": canonical_content(approval_policy),
        "model": model,
        "prompt_version": prompt_version,
        "validator_version": validator_version,
        "canonicalizer_version": canonicalizer_version,
        "privacy": canonical_content(privacy),
    }
    return _sha256_json(payload)


def build_analysis_plan(
    *,
    draft: AnalysisPlanDraft,
    user_id: str,
    workspace_id: str,
    revision: int,
    approval_policy: ApprovalPolicy,
    diagnostics: PlanDiagnostics,
    model: str,
    prompt_version: str = PLANNER_PROMPT_VERSION,
    validator_version: str = PLAN_VALIDATOR_VERSION,
    canonicalizer_version: str = PLAN_CANONICALIZER_VERSION,
    privacy: PrivacySummary | None = None,
    token_usage: TokenUsage | None = None,
    token_usage_by_stage: dict[str, StageTokenUsage] | None = None,
    now: datetime | None = None,
) -> AnalysisPlan:
    created_at = now or utc_now()
    approval = (
        PlanApprovalRecord(
            status=PlanApprovalStatus.PENDING,
            requested_at=created_at,
        )
        if approval_policy.plan_approval_required
        else PlanApprovalRecord(status=PlanApprovalStatus.NOT_REQUIRED)
    )
    status = (
        AnalysisPlanStatus.AWAITING_PLAN_APPROVAL
        if approval_policy.plan_approval_required
        else AnalysisPlanStatus.READY
    )
    provisional = {
        **draft.model_dump(mode="python"),
        "plan_id": str(uuid5(NAMESPACE_URL, f"pending:{draft.run_id}:{revision}")),
        "user_id": user_id,
        "workspace_id": workspace_id,
        "revision": revision,
        "status": status,
        "approval_policy": approval_policy,
        "approval": approval,
        "diagnostics": diagnostics,
        "model": model,
        "prompt_version": prompt_version,
        "validator_version": validator_version,
        "canonicalizer_version": canonicalizer_version,
        "privacy": privacy or PrivacySummary(),
        "plan_hash": "0" * 64,
        "write_target_keys": workbook_write_target_keys(draft.write_intents),
        "reservation_active": bool(workbook_write_target_keys(draft.write_intents)),
        "token_usage": token_usage or TokenUsage(),
        "token_usage_by_stage": token_usage_by_stage or {},
        "created_at": created_at,
        "updated_at": created_at,
    }
    plan_hash = _canonical_plan_content_hash(
        draft=draft,
        user_id=user_id,
        workspace_id=workspace_id,
        revision=revision,
        approval_policy=approval_policy,
        model=model,
        prompt_version=prompt_version,
        validator_version=validator_version,
        canonicalizer_version=canonicalizer_version,
        privacy=privacy or PrivacySummary(),
    )
    provisional["plan_hash"] = plan_hash
    provisional["plan_id"] = str(
        uuid5(
            NAMESPACE_URL,
            f"docmind:analysis-plan:{draft.run_id}:{revision}:{plan_hash}",
        )
    )
    return AnalysisPlan.model_validate(provisional)


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "AggregateMetric",
    "AggregateStep",
    "AnalysisPlan",
    "AnalysisPlanDraft",
    "AnalysisPlanStatus",
    "ApprovalPolicy",
    "ApprovalReason",
    "ArtifactWriteIntent",
    "ComparisonOperator",
    "ComparisonPredicate",
    "ComposeResponseStep",
    "DeduplicateStep",
    "DeriveColumnStep",
    "ExpectedArtifact",
    "FillMissingStep",
    "FillRule",
    "FilterRowsStep",
    "FinalPatchApprovalCommand",
    "FinalPatchProposal",
    "GenerateDatasetStep",
    "JoinStep",
    "LegacyFillMissingStep",
    "LegacyJoinStep",
    "LegacyPivotStep",
    "LEGACY_PLAN_VERSION",
    "NullPredicate",
    "PatchImpactSummary",
    "PLAN_VERSION",
    "PLAN_CANONICALIZER_VERSION",
    "PLAN_VALIDATOR_VERSION",
    "PLANNER_PROMPT_VERSION",
    "PlanApprovalCommand",
    "PlanApprovalRecord",
    "PlanApprovalStatus",
    "PlanRejectionReason",
    "PlanAssertion",
    "PlanColumn",
    "PlanDataType",
    "PlanDatasetProvenance",
    "PlanDiagnostics",
    "PlanExecutor",
    "PlanInputDataset",
    "PlanProposal",
    "PlanStep",
    "PlanStepEstimate",
    "PlanWriteIntent",
    "PivotCategoryPolicy",
    "PredicateValueType",
    "RenameColumnsStep",
    "SelectColumnsStep",
    "SetPredicate",
    "SortRowsStep",
    "StatisticalTestStep",
    "StepProvenance",
    "step_input_aliases",
    "join_output_schema",
    "TrainModelStep",
    "UnpivotStep",
    "VisualizationStep",
    "WorkbookCollisionPolicy",
    "WorkbookPlacementPolicy",
    "WorkbookVersionGuard",
    "WorkbookWriteIntent",
    "WorkbookWriteTarget",
    "analysis_plan_hash",
    "build_analysis_plan",
    "compute_input_signature",
    "workbook_write_target_keys",
]
