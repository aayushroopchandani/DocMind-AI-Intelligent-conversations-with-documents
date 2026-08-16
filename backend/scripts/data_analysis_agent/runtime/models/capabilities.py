from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator


CAPABILITY_PROFILE = "native_spreadsheet_v1"
CAPABILITY_PROFILE_VERSION = "1.0"

NATIVE_SPREADSHEET_OPERATIONS = (
    "generate_dataset",
    "filter_rows",
    "sort_rows",
    "select_columns",
    "rename_columns",
    "fill_missing",
    "deduplicate",
    "derive_column",
    "aggregate",
    "join",
    "pivot",
    "unpivot",
    "compose_response",
)


class ExecutorCapabilities(BaseModel):
    """Versioned planner/executor contract for the installed application."""

    capability_profile: Literal[CAPABILITY_PROFILE] = CAPABILITY_PROFILE
    profile_version: Literal[CAPABILITY_PROFILE_VERSION] = CAPABILITY_PROFILE_VERSION
    native_execution: bool = True
    python_execution: Literal[False] = False
    frontend_execution: bool = True
    external_network: Literal[False] = False
    workbook_patches: bool = True
    spreadsheet_formulas: bool = True
    charts: Literal[False] = False
    images: Literal[False] = False
    machine_learning: Literal[False] = False
    # Planning can produce native v2 recipes from 9.1/9.2, but the engine that
    # runs them arrives in 9.4. Execution admission reads this flag to decide
    # whether a validated plan enters the queue or completes at plan_ready, so
    # a deployment without the engine never parks runs in an undrained queue.
    # See runtime/execution/admission.py.
    native_execution_ready: bool = False
    # The same staging gate for the workbook patch protocol. Executing an edit
    # plan before a patch can be applied would produce a result with nowhere to
    # go, so admission stops such plans at plan_ready until this is true.
    workbook_patches_ready: bool = False
    supported_plan_schema_versions: tuple[str, ...] = ("2.0",)
    supported_patch_schema_versions: tuple[str, ...] = ("1.0",)
    supported_operations: tuple[str, ...] = NATIVE_SPREADSHEET_OPERATIONS

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        if self.native_execution_ready and not self.native_execution:
            raise ValueError("native execution readiness requires native capability")
        if self.workbook_patches_ready and not self.workbook_patches:
            raise ValueError("patch readiness requires the workbook patch capability")
        if len(self.supported_operations) != len(set(self.supported_operations)):
            raise ValueError("supported operations must be unique")
        unsupported = set(self.supported_operations).difference(
            NATIVE_SPREADSHEET_OPERATIONS
        )
        if unsupported:
            raise ValueError(
                "native_spreadsheet_v1 contains unsupported operations: "
                + ", ".join(sorted(unsupported))
            )
        if "2.0" not in self.supported_plan_schema_versions:
            raise ValueError("native_spreadsheet_v1 must support Plan Schema 2.0")
        return self

    def supports_plan_version(self, plan_version: str) -> bool:
        return plan_version in self.supported_plan_schema_versions

    def supports_operation(self, operation: str) -> bool:
        return operation in self.supported_operations


__all__ = [
    "CAPABILITY_PROFILE",
    "CAPABILITY_PROFILE_VERSION",
    "ExecutorCapabilities",
    "NATIVE_SPREADSHEET_OPERATIONS",
]
