from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .datasets import DatasetHandle
from .privacy import AnalysisPrivacyMode
from .runs import AnalysisMode
from .workbook import SpreadsheetContext


_DOCUMENT_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"


class ActiveArtifactContext(BaseModel):
    client_artifact_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    )
    artifact_id: str | None = Field(
        default=None,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    )
    artifact_version_id: str | None = Field(
        default=None,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    )
    artifact_type: Literal["spreadsheet", "pdf", "csv", "xlsx"]
    name: str = Field(min_length=1, max_length=255)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @model_validator(mode="after")
    def validate_server_identity(self) -> Self:
        if (self.artifact_id is None) != (self.artifact_version_id is None):
            raise ValueError(
                "artifact_id and artifact_version_id must be supplied together"
            )
        return self


class PdfRunContext(BaseModel):
    document_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    chat_id: str | None = Field(
        default=None,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    )
    active_document_id: str | None = Field(default=None, max_length=64)
    current_page: int | None = Field(default=None, ge=1)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @field_validator("document_ids", mode="before")
    @classmethod
    def normalize_documents(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("document_ids must be a list or tuple")
        output: list[str] = []
        for item in value:
            document_id = str(item or "").strip().casefold()
            if not _DOCUMENT_ID_RE.fullmatch(document_id):
                raise ValueError("document IDs must be SHA-256 digests")
            if document_id not in output:
                output.append(document_id)
        return tuple(output)

    @field_validator("active_document_id", mode="before")
    @classmethod
    def normalize_active_document(cls, value: object) -> str | None:
        if value is None:
            return None
        document_id = str(value).strip().casefold()
        if not _DOCUMENT_ID_RE.fullmatch(document_id):
            raise ValueError("active_document_id must be a SHA-256 digest")
        return document_id

    @model_validator(mode="after")
    def validate_active_document(self) -> Self:
        if (
            self.active_document_id is not None
            and self.active_document_id not in self.document_ids
        ):
            raise ValueError("active_document_id must be selected")
        return self


class AnalysisClientCapabilities(BaseModel):
    sse: bool = True
    workbook_engine: str | None = Field(default=None, max_length=100)
    workbook_engine_version: str | None = Field(default=None, max_length=100)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class CreateAnalysisRunRequest(BaseModel):
    """Authenticated request body. User identity always comes from the BFF."""

    request_version: Literal["1"] = "1"
    workspace_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    )
    mode: AnalysisMode
    prompt: str = Field(min_length=1, max_length=20_000)
    privacy_mode: AnalysisPrivacyMode = AnalysisPrivacyMode.STANDARD
    active_artifact: ActiveArtifactContext | None = None
    spreadsheet_context: SpreadsheetContext | None = None
    pdf_context: PdfRunContext | None = None
    selected_document_ids: tuple[str, ...] = Field(default=(), max_length=20)
    client_capabilities: AnalysisClientCapabilities = Field(
        default_factory=AnalysisClientCapabilities
    )

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    @field_validator("selected_document_ids", mode="before")
    @classmethod
    def normalize_documents(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        return PdfRunContext.normalize_documents(value)

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        pdf_ids = self.pdf_context.document_ids if self.pdf_context else ()
        selected = tuple(dict.fromkeys((*self.selected_document_ids, *pdf_ids)))
        object.__setattr__(self, "selected_document_ids", selected)
        if not selected and self.spreadsheet_context is None:
            raise ValueError(
                "an analysis run requires PDF documents or spreadsheet context"
            )
        if self.active_artifact is not None:
            if (
                self.active_artifact.artifact_type == "spreadsheet"
                and self.spreadsheet_context is None
            ):
                raise ValueError(
                    "an active spreadsheet requires spreadsheet_context"
                )
            if (
                self.active_artifact.artifact_type == "pdf"
                and not selected
            ):
                raise ValueError("an active PDF requires selected document IDs")
        return self


class ResolvedAnalysisInput(BaseModel):
    """Internal immutable sources attached to a durable run."""

    selected_document_ids: tuple[str, ...] = Field(default=(), max_length=20)
    dataset_handles: tuple[DatasetHandle, ...] = Field(default=(), max_length=100)
    chat_id: str | None = Field(default=None, max_length=200)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        if not self.selected_document_ids and not self.dataset_handles:
            raise ValueError("resolved analysis input cannot be empty")
        return self


def request_fingerprint(request: CreateAnalysisRunRequest) -> str:
    """Hash semantic input only; omit volatile values and complete cell rows."""

    spreadsheet = request.spreadsheet_context
    payload = {
        "request_version": request.request_version,
        "workspace_id": request.workspace_id,
        "mode": request.mode.value,
        "prompt": request.prompt,
        "privacy_mode": request.privacy_mode.value,
        "active_artifact": (
            request.active_artifact.model_dump(mode="json")
            if request.active_artifact
            else None
        ),
        "spreadsheet_context": (
            {
                "workbook_id": spreadsheet.workbook_id,
                "workbook_name": spreadsheet.workbook_name,
                "client_revision": spreadsheet.client_revision,
                "worksheet_id": spreadsheet.worksheet_id,
                "worksheet_name": spreadsheet.worksheet_name,
                "selected_range": spreadsheet.selected_range,
                "used_range": spreadsheet.used_range,
                "snapshot_range": spreadsheet.snapshot_range,
                "snapshot_hash": spreadsheet.snapshot_hash,
                "snapshot_artifact_version_id": (
                    spreadsheet.snapshot_artifact_version_id
                ),
                "locale": spreadsheet.locale,
                "timezone": spreadsheet.timezone,
            }
            if spreadsheet
            else None
        ),
        "selected_document_ids": list(request.selected_document_ids),
        "pdf_context": (
            request.pdf_context.model_dump(
                mode="json",
                exclude={"current_page"},
            )
            if request.pdf_context
            else None
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ActiveArtifactContext",
    "AnalysisClientCapabilities",
    "CreateAnalysisRunRequest",
    "PdfRunContext",
    "ResolvedAnalysisInput",
    "request_fingerprint",
]
