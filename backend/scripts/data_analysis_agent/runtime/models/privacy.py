from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AnalysisPrivacyMode(str, Enum):
    """How representative dataset values may be exposed to external models."""

    STANDARD = "standard"
    SCHEMA_ONLY = "schema_only"
    LOCAL_ONLY = "local_only"


class DataSensitivity(str, Enum):
    NONE = "none"
    EMAIL = "email"
    PHONE = "phone"
    IDENTIFIER = "identifier"
    CREDENTIAL = "credential"


class PrivacySummary(BaseModel):
    """Bounded, value-free record of privacy decisions made for one run."""

    mode: AnalysisPrivacyMode = AnalysisPrivacyMode.STANDARD
    columns_inspected: int = Field(default=0, ge=0)
    sensitive_column_count: int = Field(default=0, ge=0)
    examples_inspected: int = Field(default=0, ge=0)
    examples_redacted: int = Field(default=0, ge=0)
    hidden_rows_excluded: int = Field(default=0, ge=0)
    hidden_columns_excluded: int = Field(default=0, ge=0)
    redacted_column_keys: tuple[str, ...] = Field(default=(), max_length=500)
    classifications: dict[str, DataSensitivity] = Field(
        default_factory=dict,
    )

    model_config = ConfigDict(extra="forbid", frozen=True)


__all__ = [
    "AnalysisPrivacyMode",
    "DataSensitivity",
    "PrivacySummary",
]
