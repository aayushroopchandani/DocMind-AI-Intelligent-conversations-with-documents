"""Private artifact upload/download API for the Phase 8 analysis runtime."""

from __future__ import annotations

from typing import Annotated, Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict

from apis.deps import current_user_id, verify_internal_secret
from config.settings import settings
from scripts.data_analysis_agent.runtime.models import (
    ArtifactSource,
    ArtifactVersion,
    ArtifactVersionStatus,
    WorkspaceArtifact,
    WorkspaceArtifactType,
)
from scripts.data_analysis_agent.runtime.repositories.artifacts import (
    ArtifactRepositoryError,
    ArtifactStateConflictError,
)
from scripts.data_analysis_agent.runtime.services.artifacts import (
    ArtifactServiceError,
    ArtifactUploadFailedError,
    ArtifactVersionInProgressError,
    ArtifactVersionService,
    CreateArtifactVersion,
)
from scripts.data_analysis_agent.runtime.storage.validation import (
    ArtifactValidationError,
)


router = APIRouter(prefix="/analysis/artifacts", tags=["analysis-artifacts"])
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"

UploadIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
        pattern=r"^[!-~]{8,200}$",
    ),
]

_UPLOADABLE_TYPES = frozenset(
    {
        WorkspaceArtifactType.SPREADSHEET,
        WorkspaceArtifactType.CSV,
        WorkspaceArtifactType.XLSX,
        WorkspaceArtifactType.JSON,
        WorkspaceArtifactType.DATASET,
    }
)


class AnalysisArtifactService(Protocol):
    async def create_version(
        self,
        request: CreateArtifactVersion,
    ) -> object: ...

    async def signed_download_url(
        self,
        *,
        user_id: str,
        workspace_id: str,
        version_id: str,
        expires_in_seconds: int = 900,
    ) -> str: ...


def get_analysis_artifact_service(
    request: Request,
) -> AnalysisArtifactService:
    service = getattr(request.app.state, "analysis_artifact_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis artifact storage is unavailable",
        )
    return cast(AnalysisArtifactService, service)


class UploadedArtifactView(BaseModel):
    artifact_id: str
    workspace_id: str
    artifact_type: WorkspaceArtifactType
    name: str
    current_version_id: str | None
    version_id: str
    version_number: int
    status: ArtifactVersionStatus
    content_hash: str
    byte_count: int
    content_type: str
    filename: str

    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def from_models(
        cls,
        artifact: WorkspaceArtifact,
        version: ArtifactVersion,
    ) -> "UploadedArtifactView":
        return cls(
            artifact_id=artifact.artifact_id,
            workspace_id=artifact.workspace_id,
            artifact_type=artifact.artifact_type,
            name=artifact.name,
            current_version_id=artifact.current_version_id,
            version_id=version.version_id,
            version_number=version.version_number,
            status=version.status,
            content_hash=version.content_hash,
            byte_count=version.byte_count,
            content_type=version.content_type,
            filename=version.filename,
        )


class SignedArtifactDownload(BaseModel):
    version_id: str
    url: str
    expires_in_seconds: int

    model_config = ConfigDict(extra="forbid", frozen=True)


def _upload_version_id(
    *,
    user_id: str,
    workspace_id: str,
    artifact_id: str,
    idempotency_key: str,
) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            (
                "docmind:artifact-upload:"
                f"{user_id}:{workspace_id}:{artifact_id}:{idempotency_key}"
            ),
        )
    )


def _raise_artifact_error(exc: Exception) -> None:
    if isinstance(
        exc,
        (
            ArtifactStateConflictError,
            ArtifactVersionInProgressError,
        ),
    ):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ArtifactValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, ArtifactUploadFailedError):
        raise HTTPException(
            status_code=503,
            detail="Artifact storage could not verify the upload",
        ) from exc
    if isinstance(exc, (ArtifactRepositoryError, ArtifactServiceError)):
        raise HTTPException(
            status_code=503,
            detail="Analysis artifact storage is temporarily unavailable",
        ) from exc
    raise exc


@router.post(
    "",
    response_model=UploadedArtifactView,
    status_code=status.HTTP_201_CREATED,
)
async def upload_analysis_artifact(
    idempotency_key: UploadIdempotencyKey,
    workspace_id: str = Form(
        ...,
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    ),
    artifact_id: str = Form(
        ...,
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    ),
    artifact_type: WorkspaceArtifactType = Form(...),
    artifact_name: str = Form(..., min_length=1, max_length=255),
    parent_version_id: str | None = Form(
        default=None,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    ),
    workbook_id: str | None = Form(
        default=None,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    ),
    worksheet_id: str | None = Form(
        default=None,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    ),
    snapshot_range: str | None = Form(default=None, max_length=100),
    snapshot_hash: str | None = Form(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    ),
    client_revision: int | None = Form(default=None, ge=0),
    file: UploadFile = File(...),
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    service: AnalysisArtifactService = Depends(
        get_analysis_artifact_service
    ),
) -> UploadedArtifactView:
    if artifact_type not in _UPLOADABLE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This artifact type cannot be uploaded for analysis",
        )
    content = await file.read(settings.analysis_max_artifact_bytes + 1)
    await file.close()
    if len(content) > settings.analysis_max_artifact_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Artifact exceeds the configured upload limit",
        )
    snapshot_fields = {
        "workbook_id": workbook_id,
        "worksheet_id": worksheet_id,
        "range": snapshot_range,
        "snapshot_hash": snapshot_hash,
        "client_revision": client_revision,
    }
    supplied_snapshot_fields = {
        key for key, value in snapshot_fields.items() if value is not None
    }
    if supplied_snapshot_fields:
        if len(supplied_snapshot_fields) != len(snapshot_fields):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="All workbook snapshot metadata fields are required",
            )
        if artifact_type != WorkspaceArtifactType.SPREADSHEET:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Workbook snapshot metadata requires a spreadsheet artifact",
            )
        filename = (file.filename or "").casefold()
        if not filename.endswith((".json", ".json.gz", ".json.gzip")):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Workbook snapshots must be JSON or JSON.GZ",
            )
    try:
        result = await service.create_version(
            CreateArtifactVersion(
                user_id=user_id,
                workspace_id=workspace_id,
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                artifact_name=artifact_name,
                source=ArtifactSource.UPLOADED,
                filename=file.filename or "artifact",
                content_type=file.content_type,
                content=content,
                version_id=_upload_version_id(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    artifact_id=artifact_id,
                    idempotency_key=idempotency_key,
                ),
                parent_version_id=parent_version_id,
                metadata=(
                    snapshot_fields
                    if supplied_snapshot_fields
                    else {}
                ),
            )
        )
    except Exception as exc:
        _raise_artifact_error(exc)
        raise  # pragma: no cover
    artifact = getattr(result, "artifact")
    version = getattr(result, "version")
    return UploadedArtifactView.from_models(artifact, version)


@router.get(
    "/versions/{version_id}/download-url",
    response_model=SignedArtifactDownload,
)
async def get_analysis_artifact_download(
    response: Response,
    version_id: str = Path(
        ...,
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    ),
    workspace_id: str = Query(
        ...,
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    ),
    expires_in_seconds: int = Query(default=900, ge=60, le=3600),
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
    service: AnalysisArtifactService = Depends(
        get_analysis_artifact_service
    ),
) -> SignedArtifactDownload:
    try:
        url = await service.signed_download_url(
            user_id=user_id,
            workspace_id=workspace_id,
            version_id=version_id,
            expires_in_seconds=expires_in_seconds,
        )
    except Exception as exc:
        _raise_artifact_error(exc)
        raise  # pragma: no cover
    response.headers["Cache-Control"] = "no-store, private"
    return SignedArtifactDownload(
        version_id=version_id,
        url=url,
        expires_in_seconds=expires_in_seconds,
    )


__all__ = [
    "AnalysisArtifactService",
    "SignedArtifactDownload",
    "UploadedArtifactView",
    "get_analysis_artifact_service",
    "router",
]
