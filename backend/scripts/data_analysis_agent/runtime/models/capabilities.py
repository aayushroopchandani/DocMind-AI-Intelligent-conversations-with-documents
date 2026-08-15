from __future__ import annotations

from typing import Literal, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


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
    native_execution: bool = Field(
        default=True,
        validation_alias=AliasChoices("native_execution", "native"),
    )
    python_execution: Literal[False] = Field(
        default=False,
        validation_alias=AliasChoices("python_execution", "python"),
    )
    frontend_execution: bool = Field(
        default=True,
        validation_alias=AliasChoices("frontend_execution", "frontend"),
    )
    external_network: Literal[False] = False
    workbook_patches: bool = True
    spreadsheet_formulas: bool = True
    charts: Literal[False] = False
    images: Literal[False] = False
    machine_learning: Literal[False] = False
    # The Phase 9.1/9.2 planner may produce native v2 recipes. The physical
    # engine is enabled only in 9.4; admission must check this separate flag.
    native_execution_ready: bool = False
    supported_plan_schema_versions: tuple[str, ...] = ("2.0",)
    supported_patch_schema_versions: tuple[str, ...] = ("1.0",)
    supported_operations: tuple[str, ...] = NATIVE_SPREADSHEET_OPERATIONS
    supported_python_packages: tuple[str, ...] = ()

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        if self.python_execution or self.supported_python_packages:
            raise ValueError("Phase 9 native capability profiles cannot enable Python")
        if self.native_execution_ready and not self.native_execution:
            raise ValueError("native execution readiness requires native capability")
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

    @property
    def native(self) -> bool:
        return self.native_execution

    @property
    def python(self) -> bool:
        return self.python_execution

    @property
    def frontend(self) -> bool:
        return self.frontend_execution

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
