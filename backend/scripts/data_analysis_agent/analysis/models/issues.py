from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class IssueSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class IssueStage(str, Enum):
    RETRIEVAL = "retrieval"
    HYDRATION = "hydration"
    PROFILING = "profiling"


class IssueCode(str, Enum):
    RETRIEVAL_FAILED = "retrieval_failed"
    HYDRATION_FAILED = "hydration_failed"
    TABLE_NOT_AVAILABLE = "table_not_available"
    INVALID_TABLE = "invalid_table"
    STALE_RETRIEVAL_METADATA = "stale_retrieval_metadata"
    EMPTY_TABLE = "empty_table"
    DOCUMENT_METADATA_NOT_FOUND = "document_metadata_not_found"
    DOCUMENT_NOT_READY = "document_not_ready"
    PROFILE_CACHE_READ_FAILED = "profile_cache_read_failed"
    PROFILE_CACHE_WRITE_FAILED = "profile_cache_write_failed"
    PROFILE_DATA_LOAD_FAILED = "profile_data_load_failed"
    DATASET_NOT_AVAILABLE = "dataset_not_available"
    DATASET_VERSION_MISMATCH = "dataset_version_mismatch"
    DATASET_PROFILE_FAILED = "dataset_profile_failed"


class AnalysisIssue(BaseModel):
    """Machine-readable problem produced by a current analysis stage."""

    code: IssueCode
    severity: IssueSeverity
    stage: IssueStage
    message: str
    retryable: bool = False
    dataset_id: str | None = None
    table_id: str | None = None
    document_id: str | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")
