"""Spreadsheet import and export for the data-analysis workspace.

Deliberately stateless. Conversion is a pure function of the bytes it is
given, so these endpoints touch neither MongoDB nor Cloudinary and stay
available when the Phase 8 analysis runtime is not configured. Storing an
imported file as a durable artifact is a separate concern, already served by
`/analysis/artifacts`, and the client can do both when it wants lineage.

Untrusted bytes go through the same `validate_artifact` guard the artifact
upload path uses — zip-shape checks, macro rejection and decompression
bounds — before openpyxl is allowed anywhere near them.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from apis.deps import current_user_id, verify_internal_secret
from config.settings import settings
from scripts.data_analysis_agent.runtime.storage.validation import (
    ArtifactValidationError,
    ArtifactValidationLimits,
    sanitize_filename,
    validate_artifact,
)
from scripts.data_analysis_agent.spreadsheet_io import (
    SpreadsheetConversionError,
    SpreadsheetLimits,
    WorkbookDocument,
    read_xlsx,
    write_xlsx,
)
from scripts.data_analysis_agent.spreadsheet_io.csv_reader import read_csv


router = APIRouter(prefix="/analysis/spreadsheets", tags=["analysis-spreadsheets"])

_XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _conversion_limits() -> SpreadsheetLimits:
    """Conversion caps, driven by settings so deployments can tune them."""

    return SpreadsheetLimits(
        max_sheets=settings.analysis_spreadsheet_max_sheets,
        max_cells_per_sheet=settings.analysis_spreadsheet_max_cells,
        max_cells_total=settings.analysis_spreadsheet_max_cells,
    )


def _validation_limits() -> ArtifactValidationLimits:
    return ArtifactValidationLimits(
        max_upload_bytes=settings.analysis_max_spreadsheet_bytes,
        max_archive_uncompressed_bytes=(
            settings.analysis_max_xlsx_uncompressed_bytes
        ),
    )


class ImportedWorkbook(BaseModel):
    """Import response: the converted workbook plus what it cost."""

    filename: str
    document: WorkbookDocument
    sheet_count: int
    cell_count: int

    model_config = ConfigDict(extra="forbid")


class ExportRequest(BaseModel):
    filename: str = Field(default="workbook.xlsx", min_length=1, max_length=200)
    document: WorkbookDocument

    model_config = ConfigDict(extra="forbid")


def _raise_conversion_error(exc: Exception) -> None:
    if isinstance(exc, ArtifactValidationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    if isinstance(exc, SpreadsheetConversionError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    raise exc


@router.post("/import", response_model=ImportedWorkbook)
async def import_spreadsheet(
    file: UploadFile = File(...),
    sheet_name: Annotated[str, Form(min_length=1, max_length=64)] = "Sheet1",
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> ImportedWorkbook:
    """Convert an uploaded `.xlsx` or `.csv` into the interchange model."""

    limit = settings.analysis_max_spreadsheet_bytes
    content = await file.read(limit + 1)
    await file.close()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The uploaded spreadsheet is empty.",
        )
    if len(content) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Spreadsheets up to {limit // (1024 * 1024)} MB can be "
                "imported."
            ),
        )

    try:
        validated = validate_artifact(
            content,
            filename=file.filename or "spreadsheet",
            content_type=file.content_type,
            limits=_validation_limits(),
        )
        if validated.kind == "xlsx":
            document = read_xlsx(
                content, name=validated.filename, limits=_conversion_limits()
            )
        elif validated.kind == "csv":
            document = read_csv(
                content,
                name=validated.filename,
                sheet_name=sheet_name,
                limits=_conversion_limits(),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Only XLSX and CSV spreadsheets can be imported.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        _raise_conversion_error(exc)
        raise  # pragma: no cover

    return ImportedWorkbook(
        filename=validated.filename,
        document=document,
        sheet_count=len(document.sheets),
        cell_count=document.cell_count,
    )


@router.post(
    "/export",
    responses={200: {"content": {_XLSX_CONTENT_TYPE: {}}}},
    response_class=Response,
)
async def export_spreadsheet(
    request: ExportRequest,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> Response:
    """Render the interchange model as a downloadable `.xlsx` file."""

    try:
        filename = sanitize_filename(request.filename)
        content = write_xlsx(request.document, limits=_conversion_limits())
    except Exception as exc:
        _raise_conversion_error(exc)
        raise  # pragma: no cover

    if not filename.casefold().endswith(".xlsx"):
        filename = f"{filename}.xlsx"

    return Response(
        content=content,
        media_type=_XLSX_CONTENT_TYPE,
        headers={
            # `filename*` carries the UTF-8 form for non-ASCII names; the
            # sanitized ASCII fallback keeps older clients happy.
            "content-disposition": (
                f'attachment; filename="{filename}"; '
                f"filename*=UTF-8''{filename}"
            ),
            "content-length": str(len(content)),
            "cache-control": "no-store",
        },
    )
