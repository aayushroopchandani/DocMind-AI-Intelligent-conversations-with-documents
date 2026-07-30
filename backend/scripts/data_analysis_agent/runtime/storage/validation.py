from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import posixpath
import re
import stat
import threading
import unicodedata
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Final, Literal


ArtifactKind = Literal["csv", "xlsx", "json"]

CSV_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "text/csv",
        "application/csv",
        "text/plain",
        "application/octet-stream",
    }
)
XLSX_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/octet-stream",
    }
)
JSON_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/json",
        "text/json",
        "text/plain",
        "application/octet-stream",
    }
)
GZIP_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/gzip",
        "application/x-gzip",
        "application/octet-stream",
    }
)

_SAFE_FILENAME_CHARACTER = re.compile(r"[^A-Za-z0-9._ -]+")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_CSV_FIELD_LIMIT_LOCK = threading.Lock()
_XLSX_REQUIRED_MEMBERS: Final[frozenset[str]] = frozenset(
    {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml"}
)
_MACRO_MEMBER_MARKERS: Final[tuple[str, ...]] = (
    "vbaproject.bin",
    "vbaprojectsignature.bin",
    "xl/activex/",
    "xl/embeddings/",
)
_MACRO_CONTENT_TYPE_MARKERS: Final[tuple[bytes, ...]] = (
    b"application/vnd.ms-office.vbaproject",
    b"application/vnd.ms-excel.sheet.macroenabled",
    b"application/vnd.ms-excel.template.macroenabled",
    b"application/vnd.ms-excel.addin.macroenabled",
)


class ArtifactValidationError(ValueError):
    """Raised when uploaded artifact bytes are unsafe or unsupported."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ArtifactValidationProfile(str, Enum):
    """Select the compression checks appropriate for an artifact producer."""

    UNTRUSTED_UPLOAD = "untrusted_upload"
    TRUSTED_SERVER_GENERATED = "trusted_server_generated"


@dataclass(frozen=True, slots=True)
class ArtifactValidationLimits:
    """Resource limits applied before an artifact can enter blob storage."""

    max_upload_bytes: int = 25 * 1024 * 1024
    max_csv_rows: int = 1_000_000
    max_csv_columns: int = 2_000
    max_csv_field_characters: int = 1_000_000
    max_json_depth: int = 100
    max_json_items: int = 2_000_000
    max_archive_members: int = 10_000
    max_archive_member_bytes: int = 50 * 1024 * 1024
    max_archive_uncompressed_bytes: int = 100 * 1024 * 1024
    max_compression_ratio: float = 200.0

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_upload_bytes,
            self.max_csv_rows,
            self.max_csv_columns,
            self.max_csv_field_characters,
            self.max_json_depth,
            self.max_json_items,
            self.max_archive_members,
            self.max_archive_member_bytes,
            self.max_archive_uncompressed_bytes,
        )
        if any(value <= 0 for value in integer_limits):
            raise ValueError("artifact validation limits must be positive")
        if self.max_compression_ratio <= 1:
            raise ValueError("max_compression_ratio must be greater than one")


@dataclass(frozen=True, slots=True)
class ValidatedArtifact:
    """Canonical metadata produced by deterministic content validation."""

    filename: str
    kind: ArtifactKind
    content_type: str
    bytes: int
    sha256: str
    is_compressed: bool = False


def sanitize_filename(filename: str) -> str:
    """Return a short provider-safe basename without accepting path traversal."""

    normalized = unicodedata.normalize("NFKC", filename or "")
    # Treat backslashes as separators too; PurePath on POSIX would not.
    basename = normalized.replace("\\", "/").rsplit("/", 1)[-1]
    basename = _SAFE_FILENAME_CHARACTER.sub("_", basename).strip(" .")
    if not basename or basename in {".", ".."}:
        raise ArtifactValidationError(
            "invalid_filename", "Artifact filename is empty or invalid."
        )
    # Cloudinary object identifiers have generous limits, but keeping names
    # bounded also prevents oversized MongoDB metadata and response headers.
    if len(basename) > 180:
        stem, dot, suffix = basename.rpartition(".")
        basename = (
            f"{stem[:150]}.{suffix[:20]}" if dot and stem else basename[:180]
        )
    return basename


def validate_artifact(
    content: bytes,
    *,
    filename: str,
    content_type: str | None = None,
    limits: ArtifactValidationLimits | None = None,
    profile: ArtifactValidationProfile = ArtifactValidationProfile.UNTRUSTED_UPLOAD,
) -> ValidatedArtifact:
    """Validate supported artifact bytes without extracting files to disk."""

    active_limits = limits or ArtifactValidationLimits()
    active_profile = ArtifactValidationProfile(profile)
    safe_filename = sanitize_filename(filename)
    payload_size = len(content)
    if payload_size == 0:
        raise ArtifactValidationError("empty_file", "Artifact is empty.")
    if payload_size > active_limits.max_upload_bytes:
        raise ArtifactValidationError(
            "file_too_large",
            f"Artifact exceeds the {active_limits.max_upload_bytes}-byte limit.",
        )

    lowered = safe_filename.casefold()
    claimed_type = _normalize_content_type(content_type)
    if lowered.endswith((".xlsm", ".xltm", ".xlam")):
        raise ArtifactValidationError(
            "macro_workbook_unsupported",
            "Macro-enabled Excel workbooks are not supported.",
        )

    if lowered.endswith(".json.gz"):
        _require_content_type(claimed_type, GZIP_CONTENT_TYPES)
        json_bytes = _bounded_gzip_decompress(
            content,
            active_limits,
            enforce_compression_ratio=(
                active_profile == ArtifactValidationProfile.UNTRUSTED_UPLOAD
            ),
        )
        _validate_json(json_bytes, active_limits)
        kind: ArtifactKind = "json"
        canonical_content_type = "application/gzip"
        compressed = True
    elif lowered.endswith(".csv"):
        _require_content_type(claimed_type, CSV_CONTENT_TYPES)
        _validate_csv(content, active_limits)
        kind = "csv"
        canonical_content_type = "text/csv"
        compressed = False
    elif lowered.endswith(".xlsx"):
        _require_content_type(claimed_type, XLSX_CONTENT_TYPES)
        _validate_xlsx(content, active_limits)
        kind = "xlsx"
        canonical_content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        compressed = False
    elif lowered.endswith(".json"):
        _require_content_type(claimed_type, JSON_CONTENT_TYPES)
        _validate_json(content, active_limits)
        kind = "json"
        canonical_content_type = "application/json"
        compressed = False
    else:
        raise ArtifactValidationError(
            "unsupported_file_type",
            "Only CSV, XLSX, JSON, and JSON.GZ artifacts are supported.",
        )

    return ValidatedArtifact(
        filename=safe_filename,
        kind=kind,
        content_type=canonical_content_type,
        bytes=payload_size,
        sha256=hashlib.sha256(content).hexdigest(),
        is_compressed=compressed,
    )


def _normalize_content_type(content_type: str | None) -> str:
    return (content_type or "").partition(";")[0].strip().casefold()


def _require_content_type(
    content_type: str,
    allowed: frozenset[str],
) -> None:
    # Some browsers omit a MIME type entirely. Content validation below is
    # authoritative, so an absent type is acceptable; a contradictory one is
    # rejected rather than trusted from the extension.
    if content_type and content_type not in allowed:
        raise ArtifactValidationError(
            "content_type_mismatch",
            f"Content type {content_type!r} does not match the artifact format.",
        )


def _decode_utf8(content: bytes, *, kind: str) -> str:
    if b"\x00" in content:
        raise ArtifactValidationError(
            f"invalid_{kind}", f"{kind.upper()} content contains binary data."
        )
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ArtifactValidationError(
            f"invalid_{kind}", f"{kind.upper()} content must be UTF-8 encoded."
        ) from exc


def _validate_csv(content: bytes, limits: ArtifactValidationLimits) -> None:
    if b"\x00" in content:
        raise ArtifactValidationError(
            "invalid_csv", "CSV content contains binary data."
        )
    # `csv.field_size_limit` is process-global. Serialize only this very small
    # configuration window so concurrent validation workers cannot race it.
    with _CSV_FIELD_LIMIT_LOCK:
        previous_limit = csv.field_size_limit()
        csv.field_size_limit(limits.max_csv_field_characters)
        try:
            # TextIOWrapper decodes incrementally, avoiding a second full-size
            # string copy for large CSV uploads.
            stream = io.TextIOWrapper(
                io.BytesIO(content),
                encoding="utf-8-sig",
                errors="strict",
                newline="",
            )
            reader = csv.reader(stream, strict=True)
            saw_row = False
            for row_index, row in enumerate(reader, start=1):
                saw_row = True
                if row_index > limits.max_csv_rows:
                    raise ArtifactValidationError(
                        "csv_row_limit", "CSV contains too many rows."
                    )
                if len(row) > limits.max_csv_columns:
                    raise ArtifactValidationError(
                        "csv_column_limit", "CSV contains too many columns."
                    )
            if not saw_row:
                raise ArtifactValidationError("invalid_csv", "CSV contains no rows.")
        except (csv.Error, UnicodeDecodeError) as exc:
            raise ArtifactValidationError(
                "invalid_csv", "CSV could not be parsed."
            ) from exc
        finally:
            csv.field_size_limit(previous_limit)


def _validate_json(content: bytes, limits: ArtifactValidationLimits) -> None:
    text = _decode_utf8(content, kind="json")
    try:
        root = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ArtifactValidationError(
            "invalid_json", "JSON could not be parsed."
        ) from exc

    # Iterative traversal avoids letting a deeply nested input consume the
    # Python call stack. Scalar values still count toward the resource budget.
    item_count = 0
    stack: list[tuple[object, int]] = [(root, 1)]
    while stack:
        value, depth = stack.pop()
        item_count += 1
        if item_count > limits.max_json_items:
            raise ArtifactValidationError(
                "json_item_limit", "JSON contains too many values."
            )
        if depth > limits.max_json_depth:
            raise ArtifactValidationError(
                "json_depth_limit", "JSON is nested too deeply."
            )
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)


def _bounded_gzip_decompress(
    content: bytes,
    limits: ArtifactValidationLimits,
    *,
    enforce_compression_ratio: bool,
) -> bytes:
    if not content.startswith(b"\x1f\x8b"):
        raise ArtifactValidationError(
            "invalid_gzip", "Compressed JSON does not have a GZIP header."
        )
    output = bytearray()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(content), mode="rb") as stream:
            while chunk := stream.read(64 * 1024):
                output.extend(chunk)
                if len(output) > limits.max_archive_uncompressed_bytes:
                    raise ArtifactValidationError(
                        "archive_size_limit",
                        "Compressed JSON expands beyond the allowed size.",
                    )
                if (
                    enforce_compression_ratio
                    and len(output) > len(content) * limits.max_compression_ratio
                ):
                    raise ArtifactValidationError(
                        "archive_ratio_limit",
                        "Compressed JSON has an unsafe compression ratio.",
                    )
    except (OSError, EOFError) as exc:
        raise ArtifactValidationError(
            "invalid_gzip", "Compressed JSON could not be decompressed."
        ) from exc
    return bytes(output)


def _validate_xlsx(content: bytes, limits: ArtifactValidationLimits) -> None:
    if not content.startswith(b"PK"):
        raise ArtifactValidationError(
            "invalid_xlsx", "XLSX content is not a ZIP-based workbook."
        )

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as workbook:
            members = workbook.infolist()
            if len(members) > limits.max_archive_members:
                raise ArtifactValidationError(
                    "archive_member_limit", "Workbook contains too many ZIP members."
                )

            names: set[str] = set()
            total_compressed = 0
            total_uncompressed = 0
            for member in members:
                normalized_name = _validate_zip_member_name(member)
                names.add(normalized_name)
                lowered_name = normalized_name.casefold()
                if any(marker in lowered_name for marker in _MACRO_MEMBER_MARKERS):
                    raise ArtifactValidationError(
                        "active_content_unsupported",
                        "Workbook contains macros, ActiveX, or embedded active content.",
                    )
                if member.flag_bits & 0x1:
                    raise ArtifactValidationError(
                        "encrypted_xlsx",
                        "Encrypted or password-protected workbooks are not supported.",
                    )
                if member.file_size > limits.max_archive_member_bytes:
                    raise ArtifactValidationError(
                        "archive_member_size_limit",
                        "A workbook ZIP member expands beyond the allowed size.",
                    )
                if (
                    member.file_size > 0
                    and member.compress_size == 0
                    and not member.is_dir()
                ):
                    raise ArtifactValidationError(
                        "archive_ratio_limit",
                        "Workbook has an unsafe ZIP member compression ratio.",
                    )
                if (
                    member.compress_size > 0
                    and member.file_size / member.compress_size
                    > limits.max_compression_ratio
                ):
                    raise ArtifactValidationError(
                        "archive_ratio_limit",
                        "Workbook has an unsafe ZIP member compression ratio.",
                    )
                total_compressed += member.compress_size
                total_uncompressed += member.file_size
                if total_uncompressed > limits.max_archive_uncompressed_bytes:
                    raise ArtifactValidationError(
                        "archive_size_limit",
                        "Workbook expands beyond the allowed size.",
                    )

            if not _XLSX_REQUIRED_MEMBERS.issubset(names):
                raise ArtifactValidationError(
                    "invalid_xlsx",
                    "Workbook is missing required XLSX package members.",
                )
            if (
                total_compressed > 0
                and total_uncompressed / total_compressed
                > limits.max_compression_ratio
            ):
                raise ArtifactValidationError(
                    "archive_ratio_limit",
                    "Workbook has an unsafe total compression ratio.",
                )

            content_types = workbook.read("[Content_Types].xml")
            if any(
                marker in content_types.lower()
                for marker in _MACRO_CONTENT_TYPE_MARKERS
            ):
                raise ArtifactValidationError(
                    "macro_workbook_unsupported",
                    "Macro-enabled Excel workbooks are not supported.",
                )
            corrupt_member = workbook.testzip()
            if corrupt_member is not None:
                raise ArtifactValidationError(
                    "invalid_xlsx",
                    f"Workbook ZIP member {corrupt_member!r} failed its checksum.",
                )
    except ArtifactValidationError:
        raise
    except (zipfile.BadZipFile, KeyError, RuntimeError, OSError) as exc:
        raise ArtifactValidationError(
            "invalid_xlsx", "XLSX workbook could not be parsed safely."
        ) from exc


def _validate_zip_member_name(member: zipfile.ZipInfo) -> str:
    name = member.filename.replace("\\", "/")
    normalized = posixpath.normpath(name)
    raw_path = PurePosixPath(name)
    path = PurePosixPath(normalized)
    if (
        not name
        or name.startswith(("/", "\\"))
        or _WINDOWS_DRIVE.match(name)
        or normalized in {"", ".", ".."}
        or ".." in raw_path.parts
        or ".." in path.parts
    ):
        raise ArtifactValidationError(
            "unsafe_archive_path", "Workbook contains an unsafe ZIP member path."
        )

    unix_mode = member.external_attr >> 16
    if unix_mode and stat.S_ISLNK(unix_mode):
        raise ArtifactValidationError(
            "unsafe_archive_link", "Workbook contains a symbolic link."
        )
    return normalized
