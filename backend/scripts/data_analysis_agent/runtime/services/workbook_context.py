from __future__ import annotations

import gzip
import io
import re
from dataclasses import dataclass
from typing import Iterable
from uuid import NAMESPACE_URL, uuid5

from pydantic import JsonValue, ValidationError

from ..models import (
    ActiveArtifactContext,
    ArtifactSource,
    BlobDatasetStorage,
    DatasetCatalogEntry,
    DatasetColumn,
    DatasetColumnType,
    DatasetHandle,
    DatasetSourceType,
    SpreadsheetContext,
    SpreadsheetRangeLocator,
    TabularDataset,
    WorkbookCellType,
    WorkbookRangeSnapshot,
    WorkspaceArtifactType,
    a1_subrange,
    canonical_snapshot_bytes,
    canonical_snapshot_hash,
    tabular_source_version,
)
from ..repositories.datasets import DatasetCatalogRepository
from ..storage.validation import ArtifactValidationProfile
from .artifacts import (
    ArtifactServiceError,
    ArtifactUploadResult,
    ArtifactVersionService,
    CreateArtifactVersion,
)


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class WorkbookContextError(RuntimeError):
    """Workbook context is invalid, stale, or could not be synchronized."""


class WorkbookContextTooLargeError(WorkbookContextError):
    """Inline workbook data must be uploaded before run creation."""


@dataclass(frozen=True, slots=True)
class WorkbookContextLimits:
    max_inline_cells: int = 25_000
    max_inline_bytes: int = 5 * 1024 * 1024
    max_uploaded_cells: int = 250_000
    max_uploaded_bytes: int = 25 * 1024 * 1024
    max_uploaded_blob_bytes: int = 50 * 1024 * 1024
    max_columns: int = 500
    max_datasets: int = 50

    def __post_init__(self) -> None:
        if min(
            self.max_inline_cells,
            self.max_inline_bytes,
            self.max_uploaded_cells,
            self.max_uploaded_bytes,
            self.max_uploaded_blob_bytes,
            self.max_columns,
            self.max_datasets,
        ) <= 0:
            raise ValueError("workbook context limits must be positive")
        if self.max_columns > 500:
            raise ValueError("workbook dataset columns cannot exceed 500")


@dataclass(frozen=True, slots=True)
class ResolvedWorkbookContext:
    dataset_handles: tuple[DatasetHandle, ...]
    workbook_artifact_version_id: str
    dataset_artifact_version_ids: tuple[str, ...]
    source_artifact_version_id: str | None = None

    def __post_init__(self) -> None:
        if not self.dataset_handles:
            raise ValueError("resolved workbook context needs a dataset")
        if len(self.dataset_handles) != len(self.dataset_artifact_version_ids):
            raise ValueError(
                "dataset handles and artifact versions must have matching lengths"
            )

    @property
    def dataset_handle(self) -> DatasetHandle:
        """Compatibility accessor for callers that require a selected table."""

        return self.dataset_handles[0]

    @property
    def dataset_artifact_version_id(self) -> str:
        return self.dataset_artifact_version_ids[0]


class WorkbookContextService:
    """Synchronize a range snapshot and register one immutable dataset handle."""

    def __init__(
        self,
        *,
        artifact_service: ArtifactVersionService,
        dataset_catalog: DatasetCatalogRepository,
        limits: WorkbookContextLimits | None = None,
    ) -> None:
        self._artifact_service = artifact_service
        self._dataset_catalog = dataset_catalog
        self._limits = limits or WorkbookContextLimits()

    async def resolve(
        self,
        *,
        user_id: str,
        workspace_id: str,
        context: SpreadsheetContext,
        active_artifact: ActiveArtifactContext | None,
    ) -> ResolvedWorkbookContext:
        await self._validate_active_artifact(
            user_id=user_id,
            workspace_id=workspace_id,
            active_artifact=active_artifact,
        )
        artifact_id = _server_artifact_id(
            user_id=user_id,
            workspace_id=workspace_id,
            context=context,
            active_artifact=active_artifact,
        )
        if context.snapshot is not None:
            snapshot = context.snapshot
            canonical = canonical_snapshot_bytes(snapshot)
            if snapshot.cell_count > self._limits.max_inline_cells:
                raise WorkbookContextTooLargeError(
                    "inline workbook snapshot exceeds the cell limit"
                )
            if len(canonical) > self._limits.max_inline_bytes:
                raise WorkbookContextTooLargeError(
                    "inline workbook snapshot exceeds the byte limit"
                )
            workbook_upload = await self._upload_workbook_snapshot(
                user_id=user_id,
                workspace_id=workspace_id,
                artifact_id=artifact_id,
                context=context,
                snapshot=snapshot,
                active_artifact=active_artifact,
            )
        else:
            assert context.snapshot_artifact_version_id is not None
            snapshot = await self._load_uploaded_snapshot(
                user_id=user_id,
                workspace_id=workspace_id,
                artifact_id=artifact_id,
                version_id=context.snapshot_artifact_version_id,
                context=context,
            )
            workbook_upload = None

        workbook_version_id = (
            workbook_upload.version.version_id
            if workbook_upload is not None
            else context.snapshot_artifact_version_id
        )
        assert workbook_version_id is not None
        snapshots = _dataset_snapshots(
            snapshot,
            split_tables=context.selected_range is None,
            max_datasets=self._limits.max_datasets,
        )
        handles: list[DatasetHandle] = []
        dataset_artifact_version_ids: list[str] = []
        for dataset_snapshot in snapshots:
            tabular = _snapshot_to_tabular(
                user_id=user_id,
                workspace_id=workspace_id,
                artifact_id=artifact_id,
                artifact_version_id=workbook_version_id,
                context=context,
                snapshot=dataset_snapshot,
                max_columns=self._limits.max_columns,
            )
            dataset_upload = await self._upload_tabular_dataset(
                tabular=tabular,
                workbook_artifact_id=artifact_id,
            )
            if dataset_upload.version.blob is None:
                raise WorkbookContextError("dataset artifact is not ready")
            handle = tabular.handle(
                storage=BlobDatasetStorage(
                    artifact_version_id=dataset_upload.version.version_id,
                    blob=dataset_upload.version.blob,
                    encoding="tabular_json_gzip",
                )
            )
            registered = await self._dataset_catalog.register(
                DatasetCatalogEntry(
                    handle=handle,
                    discovery_summary=(
                        f"{context.worksheet_name} {dataset_snapshot.range_a1}: "
                        + ", ".join(
                            column.label for column in handle.columns[:12]
                        )
                    )[:1600],
                    keywords=tuple(
                        dict.fromkeys(
                            column.label
                            for column in handle.columns
                            if column.label
                        )
                    )[:40],
                )
            )
            handles.append(registered.handle)
            dataset_artifact_version_ids.append(
                dataset_upload.version.version_id
            )
        return ResolvedWorkbookContext(
            dataset_handles=tuple(handles),
            workbook_artifact_version_id=workbook_version_id,
            dataset_artifact_version_ids=tuple(
                dataset_artifact_version_ids
            ),
            source_artifact_version_id=(
                active_artifact.artifact_version_id
                if active_artifact is not None
                and active_artifact.artifact_type in {"csv", "xlsx"}
                else None
            ),
        )

    async def _validate_active_artifact(
        self,
        *,
        user_id: str,
        workspace_id: str,
        active_artifact: ActiveArtifactContext | None,
    ) -> None:
        if (
            active_artifact is None
            or active_artifact.artifact_id is None
            or active_artifact.artifact_version_id is None
        ):
            return
        try:
            version = await self._artifact_service.get_ready_version(
                user_id=user_id,
                workspace_id=workspace_id,
                version_id=active_artifact.artifact_version_id,
            )
        except ArtifactServiceError as exc:
            raise WorkbookContextError(
                "active workbook artifact version is unavailable"
            ) from exc
        if version.artifact_id != active_artifact.artifact_id:
            raise WorkbookContextError(
                "active workbook artifact and version do not match"
            )
        version_kind = str(version.metadata.get("artifact_kind") or "")
        if (
            active_artifact.artifact_type in {"csv", "xlsx"}
            and version_kind != active_artifact.artifact_type
        ):
            raise WorkbookContextError(
                "active workbook artifact type does not match its version"
            )

    async def _upload_workbook_snapshot(
        self,
        *,
        user_id: str,
        workspace_id: str,
        artifact_id: str,
        context: SpreadsheetContext,
        snapshot: WorkbookRangeSnapshot,
        active_artifact: ActiveArtifactContext | None,
    ) -> ArtifactUploadResult:
        content = gzip.compress(
            snapshot.model_dump_json().encode("utf-8"),
            mtime=0,
        )
        version_id = str(
            uuid5(
                NAMESPACE_URL,
                (
                    "docmind:workbook-snapshot:"
                    f"{user_id}:{workspace_id}:{artifact_id}:"
                    f"{context.snapshot_hash}"
                ),
            )
        )
        return await self._artifact_service.create_version(
            CreateArtifactVersion(
                user_id=user_id,
                workspace_id=workspace_id,
                artifact_id=artifact_id,
                artifact_type=WorkspaceArtifactType.SPREADSHEET,
                artifact_name=context.workbook_name,
                source=ArtifactSource.CREATED,
                filename="workbook-snapshot.json.gz",
                content_type="application/gzip",
                content=content,
                version_id=version_id,
                validation_profile=(
                    ArtifactValidationProfile.TRUSTED_SERVER_GENERATED
                ),
                metadata={
                    "workbook_id": context.workbook_id,
                    "worksheet_id": context.worksheet_id,
                    "range": context.snapshot_range,
                    "snapshot_hash": context.snapshot_hash,
                    "client_revision": context.client_revision,
                    **_source_artifact_metadata(active_artifact),
                },
            )
        )

    async def _load_uploaded_snapshot(
        self,
        *,
        user_id: str,
        workspace_id: str,
        artifact_id: str,
        version_id: str,
        context: SpreadsheetContext,
    ) -> WorkbookRangeSnapshot:
        try:
            version = await self._artifact_service.get_ready_version(
                user_id=user_id,
                workspace_id=workspace_id,
                version_id=version_id,
            )
            if version.artifact_id != artifact_id:
                raise WorkbookContextError(
                    "uploaded snapshot does not belong to the active workbook"
                )
            _validate_uploaded_snapshot_metadata(
                version_metadata=version.metadata,
                context=context,
            )
            content = await self._artifact_service.download(
                user_id=user_id,
                workspace_id=workspace_id,
                version_id=version_id,
                max_bytes=self._limits.max_uploaded_blob_bytes,
            )
            if version.filename.casefold().endswith((".json.gz", ".json.gzip")):
                decoded = _bounded_gzip_decompress(
                    content,
                    max_bytes=self._limits.max_uploaded_bytes,
                )
            elif version.filename.casefold().endswith(".json"):
                decoded = content
                if len(decoded) > self._limits.max_uploaded_bytes:
                    raise WorkbookContextTooLargeError(
                        "uploaded snapshot exceeds the expanded byte limit"
                    )
            else:
                raise WorkbookContextError(
                    "uploaded snapshot must be JSON or JSON.GZ"
                )
            snapshot = WorkbookRangeSnapshot.model_validate_json(decoded)
        except (OSError, ValidationError, ValueError) as exc:
            raise WorkbookContextError(
                "uploaded workbook snapshot is invalid"
            ) from exc
        if snapshot.cell_count > self._limits.max_uploaded_cells:
            raise WorkbookContextTooLargeError(
                "uploaded workbook snapshot exceeds the cell limit"
            )
        if snapshot.column_count > self._limits.max_columns:
            raise WorkbookContextTooLargeError(
                "uploaded workbook snapshot exceeds the column limit"
            )
        if canonical_snapshot_hash(snapshot) != context.snapshot_hash:
            raise WorkbookContextError("uploaded snapshot checksum does not match")
        return snapshot

    async def _upload_tabular_dataset(
        self,
        *,
        tabular: TabularDataset,
        workbook_artifact_id: str,
    ) -> ArtifactUploadResult:
        content = gzip.compress(
            tabular.model_dump_json().encode("utf-8"),
            mtime=0,
        )
        artifact_id = _derived_identifier(
            "dataset",
            (
                f"{tabular.user_id}:{tabular.workspace_id}:"
                f"{workbook_artifact_id}:{tabular.dataset_id}"
            ),
        )
        version_id = str(
            uuid5(
                NAMESPACE_URL,
                f"docmind:dataset-version:{tabular.source_version}",
            )
        )
        return await self._artifact_service.create_version(
            CreateArtifactVersion(
                user_id=tabular.user_id,
                workspace_id=tabular.workspace_id,
                artifact_id=artifact_id,
                artifact_type=WorkspaceArtifactType.DATASET,
                artifact_name=tabular.title,
                source=ArtifactSource.IMPORTED,
                filename="dataset-range.json.gz",
                content_type="application/gzip",
                content=content,
                version_id=version_id,
                validation_profile=(
                    ArtifactValidationProfile.TRUSTED_SERVER_GENERATED
                ),
                metadata={
                    "dataset_id": tabular.dataset_id,
                    "source_version": tabular.source_version,
                    "source_artifact_id": workbook_artifact_id,
                    "row_count": len(tabular.rows),
                    "column_count": len(tabular.columns),
                },
            )
        )


def _snapshot_to_tabular(
    *,
    user_id: str,
    workspace_id: str,
    artifact_id: str,
    artifact_version_id: str,
    context: SpreadsheetContext,
    snapshot: WorkbookRangeSnapshot,
    max_columns: int,
) -> TabularDataset:
    if snapshot.column_count > max_columns:
        raise WorkbookContextTooLargeError(
            "spreadsheet dataset exceeds the column limit"
        )
    headers = _headers(snapshot)
    # A selected range is explicit user scope. For an automatic used-range
    # snapshot, hidden cells remain in the immutable workbook artifact but are
    # not materialized into the analytical dataset or later sampled for LLMs.
    explicit_scope = context.selected_range is not None
    included_columns = tuple(
        index
        for index in range(snapshot.column_count)
        if explicit_scope or index not in snapshot.hidden_columns
    )
    if not included_columns:
        raise WorkbookContextError(
            "the spreadsheet range contains no visible analytical columns"
        )
    included_rows = tuple(
        index
        for index in range(snapshot.row_count)
        if index != snapshot.header_row_index
        and (explicit_scope or index not in snapshot.hidden_rows)
    )
    columns = tuple(
        DatasetColumn(
            key=f"c_{source_index + 1:04d}",
            label=headers[source_index],
            type=_column_type(
                snapshot,
                source_index,
                included_rows=included_rows,
            ),
            source_index=source_index,
        )
        for source_index in included_columns
    )
    rows: tuple[dict[str, JsonValue], ...] = tuple(
        {
            column.key: _json_cell(
                snapshot.values[row_index][column.source_index]
            )
            for column in columns
        }
        for row_index in included_rows
    )
    locator = SpreadsheetRangeLocator(
        artifact_id=artifact_id,
        artifact_version_id=artifact_version_id,
        workbook_id=context.workbook_id,
        workbook_revision=context.client_revision,
        worksheet_id=context.worksheet_id,
        worksheet_name=context.worksheet_name,
        range_a1=snapshot.range_a1,
        snapshot_hash=canonical_snapshot_hash(snapshot),
        hidden_rows_excluded=(
            0 if explicit_scope else len(snapshot.hidden_rows)
        ),
        hidden_columns_excluded=(
            0 if explicit_scope else len(snapshot.hidden_columns)
        ),
    )
    title = (
        f"{context.workbook_name} — {context.worksheet_name} "
        f"{snapshot.range_a1}"
    )
    version = tabular_source_version(
        source_type=DatasetSourceType.SPREADSHEET_RANGE,
        title=title,
        columns=columns,
        rows=rows,
        locator=locator,
    )
    dataset_id = str(
        uuid5(
            NAMESPACE_URL,
            (
                f"docmind:spreadsheet-dataset:{user_id}:{workspace_id}:"
                f"{artifact_id}:{artifact_version_id}:{context.worksheet_id}:"
                f"{snapshot.range_a1}:{version}"
            ),
        )
    )
    return TabularDataset(
        dataset_id=dataset_id,
        user_id=user_id,
        workspace_id=workspace_id,
        source_type=DatasetSourceType.SPREADSHEET_RANGE,
        source_version=version,
        title=title,
        columns=columns,
        rows=rows,
        locator=locator,
    )


def _dataset_snapshots(
    snapshot: WorkbookRangeSnapshot,
    *,
    split_tables: bool,
    max_datasets: int,
) -> tuple[WorkbookRangeSnapshot, ...]:
    """Split an unselected used range on fully blank row/column separators."""

    if not split_tables:
        return (snapshot,)
    occupied_rows = tuple(
        row_index
        for row_index in range(snapshot.row_count)
        if any(
            _cell_is_populated(
                snapshot.values[row_index][column_index],
                snapshot.formulas[row_index][column_index],
            )
            for column_index in range(snapshot.column_count)
        )
    )
    if not occupied_rows:
        return (snapshot,)

    regions: list[tuple[int, int, int, int]] = []
    for row_start, row_end in _contiguous_groups(occupied_rows):
        occupied_columns = tuple(
            column_index
            for column_index in range(snapshot.column_count)
            if any(
                _cell_is_populated(
                    snapshot.values[row_index][column_index],
                    snapshot.formulas[row_index][column_index],
                )
                for row_index in range(row_start, row_end + 1)
            )
        )
        for column_start, column_end in _contiguous_groups(
            occupied_columns
        ):
            regions.append(
                (row_start, row_end, column_start, column_end)
            )
            if len(regions) > max_datasets:
                raise WorkbookContextTooLargeError(
                    "workbook snapshot exceeds the detected dataset limit"
                )
    if len(regions) <= 1:
        return (snapshot,)
    return tuple(
        _slice_snapshot(
            snapshot,
            row_start=row_start,
            row_end=row_end,
            column_start=column_start,
            column_end=column_end,
        )
        for row_start, row_end, column_start, column_end in regions
    )


def _contiguous_groups(indexes: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
    if not indexes:
        return ()
    groups: list[tuple[int, int]] = []
    start = indexes[0]
    previous = start
    for index in indexes[1:]:
        if index != previous + 1:
            groups.append((start, previous))
            start = index
        previous = index
    groups.append((start, previous))
    return tuple(groups)


def _cell_is_populated(value: object, formula: str | None) -> bool:
    if formula is not None and formula.strip():
        return True
    if value is None:
        return False
    return not isinstance(value, str) or bool(value.strip())


def _slice_snapshot(
    snapshot: WorkbookRangeSnapshot,
    *,
    row_start: int,
    row_end: int,
    column_start: int,
    column_end: int,
) -> WorkbookRangeSnapshot:
    row_count = row_end - row_start + 1
    column_count = column_end - column_start + 1

    def matrix_slice(matrix: tuple[tuple[object, ...], ...]) -> tuple[
        tuple[object, ...],
        ...,
    ]:
        return tuple(
            tuple(row[column_start : column_end + 1])
            for row in matrix[row_start : row_end + 1]
        )

    values = matrix_slice(snapshot.values)
    formulas = matrix_slice(snapshot.formulas)
    cell_types = matrix_slice(snapshot.cell_types)
    number_formats = matrix_slice(snapshot.number_formats)
    explicit_header_row_index = (
        snapshot.header_row_index - row_start
        if snapshot.header_row_index is not None
        and row_start <= snapshot.header_row_index <= row_end
        else None
    )
    if explicit_header_row_index is not None:
        header_row_index = explicit_header_row_index
        column_headers = (
            snapshot.column_headers[column_start : column_end + 1]
            if snapshot.column_headers
            else _header_labels(values[header_row_index])
        )
    else:
        header_row_index = _infer_local_header_row_index(
            values=values,
            formulas=formulas,
            cell_types=cell_types,
        )
        if header_row_index is not None:
            column_headers = _header_labels(values[header_row_index])
        elif snapshot.header_row_index is None and snapshot.column_headers:
            # Header labels may be supplied independently of a worksheet row.
            # Preserve those labels only when there is no explicit global row
            # that belongs to a different detected table.
            column_headers = snapshot.column_headers[
                column_start : column_end + 1
            ]
        else:
            column_headers = ()

    return WorkbookRangeSnapshot(
        range_a1=a1_subrange(
            snapshot.range_a1,
            row_offset=row_start,
            column_offset=column_start,
            row_count=row_count,
            column_count=column_count,
        ),
        values=values,
        formulas=formulas,
        cell_types=cell_types,
        number_formats=number_formats,
        column_headers=column_headers,
        header_row_index=header_row_index,
        row_count=row_count,
        column_count=column_count,
        hidden_rows=tuple(
            index - row_start
            for index in snapshot.hidden_rows
            if row_start <= index <= row_end
        ),
        hidden_columns=tuple(
            index - column_start
            for index in snapshot.hidden_columns
            if column_start <= index <= column_end
        ),
    )


def _infer_local_header_row_index(
    *,
    values: tuple[tuple[object, ...], ...],
    formulas: tuple[tuple[object, ...], ...],
    cell_types: tuple[tuple[object, ...], ...],
) -> int | None:
    """Conservatively recognize a local header at the top of a split table.

    A text-only first row is not enough evidence because it could be ordinary
    data. At least one labelled column must transition to a typed/formula data
    value in the following bounded sample. This avoids silently dropping the
    first record from all-text or headerless datasets.
    """

    if len(values) < 2 or not values[0]:
        return None

    candidate = values[0]
    labelled_columns = tuple(
        index
        for index, value in enumerate(candidate)
        if isinstance(value, str) and value.strip()
    )
    minimum_labels = max(1, (len(candidate) + 1) // 2)
    if len(labelled_columns) < minimum_labels:
        return None
    if any(
        _cell_is_populated(value, formulas[0][index])
        and index not in labelled_columns
        for index, value in enumerate(candidate)
    ):
        return None

    normalized_labels = tuple(
        str(candidate[index]).strip().casefold()
        for index in labelled_columns
    )
    if len(normalized_labels) != len(set(normalized_labels)):
        return None

    sample_end = min(len(values), 26)
    for row_index in range(1, sample_end):
        for column_index in labelled_columns:
            value = values[row_index][column_index]
            formula = formulas[row_index][column_index]
            if not _cell_is_populated(value, formula):
                continue
            cell_type = cell_types[row_index][column_index]
            if (
                cell_type
                in {
                    WorkbookCellType.NUMBER,
                    WorkbookCellType.BOOLEAN,
                    WorkbookCellType.DATE,
                    WorkbookCellType.FORMULA,
                }
                or not isinstance(value, str)
            ):
                return 0
    return None


def _header_labels(row: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(
        str(value).strip() if value is not None else ""
        for value in row
    )


def _headers(snapshot: WorkbookRangeSnapshot) -> tuple[str, ...]:
    if snapshot.column_headers:
        raw = snapshot.column_headers
    elif snapshot.header_row_index is not None:
        raw = tuple(
            str(value).strip() if value is not None else ""
            for value in snapshot.values[snapshot.header_row_index]
        )
    else:
        raw = ()
    return tuple(
        (
            str(raw[index]).strip()
            if index < len(raw) and str(raw[index]).strip()
            else f"Column {index + 1}"
        )
        for index in range(snapshot.column_count)
    )


def _column_type(
    snapshot: WorkbookRangeSnapshot,
    column_index: int,
    *,
    included_rows: tuple[int, ...] | None = None,
) -> DatasetColumnType:
    allowed_rows = (
        frozenset(included_rows) if included_rows is not None else None
    )
    observed: list[WorkbookCellType] = []
    for row_index, row in enumerate(snapshot.cell_types):
        if row_index == snapshot.header_row_index:
            continue
        if allowed_rows is not None and row_index not in allowed_rows:
            continue
        cell_type = row[column_index]
        if cell_type not in {None, WorkbookCellType.BLANK, WorkbookCellType.ERROR}:
            observed.append(cell_type)
    if WorkbookCellType.DATE in observed:
        return DatasetColumnType.DATE
    values = [
        value
        for value in observed
        if value != WorkbookCellType.FORMULA
    ]
    if values and all(value == WorkbookCellType.NUMBER for value in values):
        return DatasetColumnType.NUMBER
    if values and all(value == WorkbookCellType.BOOLEAN for value in values):
        return DatasetColumnType.BOOLEAN
    # Formula result types are represented by the raw value matrix. Use that
    # when the engine reports only "formula".
    raw_values = tuple(
        row[column_index]
        for row_index, row in enumerate(snapshot.values)
        if row_index != snapshot.header_row_index
        and (allowed_rows is None or row_index in allowed_rows)
        and row[column_index] is not None
    )
    if raw_values and all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in raw_values
    ):
        return DatasetColumnType.NUMBER
    if raw_values and all(isinstance(value, bool) for value in raw_values):
        return DatasetColumnType.BOOLEAN
    return DatasetColumnType.STRING


def _json_cell(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _server_artifact_id(
    *,
    user_id: str,
    workspace_id: str,
    context: SpreadsheetContext,
    active_artifact: ActiveArtifactContext | None,
) -> str:
    if (
        active_artifact
        and active_artifact.artifact_id
        and active_artifact.artifact_type == "spreadsheet"
    ):
        return active_artifact.artifact_id
    if active_artifact and active_artifact.artifact_id:
        # CSV/XLSX are immutable source files, while a live Univer workbook
        # snapshot is a different logical artifact with its own version chain.
        return _derived_identifier(
            "workbook",
            (
                f"{user_id}:{workspace_id}:{active_artifact.artifact_id}:"
                f"{context.workbook_id}"
            ),
        )
    candidate = active_artifact.client_artifact_id if active_artifact else context.workbook_id
    if _SAFE_IDENTIFIER.fullmatch(candidate):
        return candidate
    return _derived_identifier(
        "artifact",
        f"{user_id}:{workspace_id}:{candidate}",
    )


def _derived_identifier(prefix: str, identity: str) -> str:
    return f"{prefix}-{uuid5(NAMESPACE_URL, identity)}"


def _source_artifact_metadata(
    active_artifact: ActiveArtifactContext | None,
) -> dict[str, JsonValue]:
    if (
        active_artifact is None
        or active_artifact.artifact_type not in {"csv", "xlsx"}
    ):
        return {}
    return {
        "source_artifact_id": active_artifact.artifact_id,
        "source_artifact_version_id": active_artifact.artifact_version_id,
        "source_artifact_type": active_artifact.artifact_type,
    }


def _bounded_gzip_decompress(content: bytes, *, max_bytes: int) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as stream:
            decoded = stream.read(max_bytes + 1)
    except OSError:
        raise
    if len(decoded) > max_bytes:
        raise WorkbookContextTooLargeError(
            "uploaded snapshot expands beyond the byte limit"
        )
    return decoded


def _validate_uploaded_snapshot_metadata(
    *,
    version_metadata: dict[str, JsonValue],
    context: SpreadsheetContext,
) -> None:
    expected: dict[str, JsonValue] = {
        "workbook_id": context.workbook_id,
        "worksheet_id": context.worksheet_id,
        "range": context.snapshot_range,
        "snapshot_hash": context.snapshot_hash,
        "client_revision": context.client_revision,
    }
    if any(version_metadata.get(key) != value for key, value in expected.items()):
        raise WorkbookContextError(
            "uploaded snapshot metadata does not match the workbook context"
        )


__all__ = [
    "ResolvedWorkbookContext",
    "WorkbookContextError",
    "WorkbookContextLimits",
    "WorkbookContextService",
    "WorkbookContextTooLargeError",
]
