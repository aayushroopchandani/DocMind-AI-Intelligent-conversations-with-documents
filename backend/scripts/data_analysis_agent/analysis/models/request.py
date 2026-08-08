from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scripts.data_analysis_agent.runtime.models.datasets import DatasetHandle
from scripts.data_analysis_agent.runtime.models.privacy import AnalysisPrivacyMode


_DOCUMENT_ID_RE = re.compile(r"^[0-9a-f]{64}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AnalysisRequest(BaseModel):
    """Authenticated, immutable input for one data-analysis run."""

    user_id: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    workspace_id: str = Field(default="", min_length=1)
    query: str = Field(min_length=1)
    privacy_mode: AnalysisPrivacyMode = AnalysisPrivacyMode.STANDARD
    document_ids: tuple[str, ...] = ()
    pinned_datasets: tuple[DatasetHandle, ...] = Field(default=(), max_length=100)
    created_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def default_workspace_to_chat(cls, value: object) -> object:
        if isinstance(value, dict) and not str(value.get("workspace_id") or "").strip():
            return {**value, "workspace_id": value.get("chat_id")}
        return value

    @field_validator(
        "user_id",
        "chat_id",
        "workspace_id",
        "query",
        mode="before",
    )
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        normalized = " ".join(str(value or "").split())
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("document_ids", mode="before")
    @classmethod
    def normalize_document_ids(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("document_ids must be a list or tuple")

        output: list[str] = []
        seen: set[str] = set()
        for item in value:
            document_id = str(item or "").strip().casefold()
            if not document_id or document_id in seen:
                continue
            if not _DOCUMENT_ID_RE.fullmatch(document_id):
                raise ValueError(
                    "analysis document_ids must be SHA-256 content identifiers"
                )
            seen.add(document_id)
            output.append(document_id)
        return tuple(output)

    @field_validator("pinned_datasets", mode="before")
    @classmethod
    def normalize_pinned_datasets(cls, value: object) -> tuple[object, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("pinned_datasets must be a list or tuple")
        return tuple(value)

    @model_validator(mode="after")
    def validate_sources(self) -> "AnalysisRequest":
        if not self.document_ids and not self.pinned_datasets:
            raise ValueError("at least one document or pinned dataset is required")
        identities: set[tuple[str, str]] = set()
        for dataset in self.pinned_datasets:
            if dataset.user_id != self.user_id:
                raise ValueError("pinned datasets must belong to the request user")
            if dataset.workspace_id != self.workspace_id:
                raise ValueError("pinned datasets must belong to the workspace")
            identity = (dataset.dataset_id, dataset.source_version)
            if identity in identities:
                raise ValueError("pinned dataset versions must be unique")
            identities.add(identity)
        return self

    @property
    def selected_source_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *self.document_ids,
                    *(
                        dataset.source_container_id
                        for dataset in self.pinned_datasets
                    ),
                )
            )
        )
