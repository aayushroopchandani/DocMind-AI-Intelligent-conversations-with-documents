"""The durable result encoding (Phase 9.9.3).

CSV alone is not a typed interchange format, so the canonical bundle is always a
pair: gzipped rows plus a schema manifest that says how to read them. The
manifest carries what CSV cannot — logical type, unit, decimal scale, timezone,
and the encodings below.

Two encodings need stating explicitly because getting either wrong corrupts data
silently on the way back:

*Null versus empty string.* CSV has no null. A null is written as the sentinel
``\\N``; an empty string is written as an empty field. A literal string that
would collide with the sentinel is escaped with a leading backslash, and any
string already starting with a backslash is escaped the same way. Decoding
reverses it, so every string survives the trip including ``\\N`` itself.

*Floats.* Written with ``repr``, which round-trips exactly in Python. The
declared decimal scale is recorded in the manifest for display and comparison
rather than applied to the stored text, so no precision is lost in storage.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from datetime import date, datetime
from typing import Any

import polars as pl

from ....runtime.models.plans import PlanColumn, PlanDataType
from ..native import semantics


RESULT_FORMAT_VERSION = "1.0"

NULL_SENTINEL = "\\N"
ESCAPE_PREFIX = "\\"

_CSV_DIALECT = {
    "delimiter": ",",
    "quotechar": '"',
    "doublequote": True,
    "lineterminator": "\n",
    "quoting": csv.QUOTE_MINIMAL,
}


class ResultSerializationError(ValueError):
    """A result cannot be encoded or decoded under the durable format."""


def encode_value(value: Any, column: PlanColumn) -> str:
    """Return the CSV field for one typed value."""

    if value is None:
        return NULL_SENTINEL
    if column.data_type is PlanDataType.BOOLEAN:
        return "true" if value else "false"
    if column.data_type is PlanDataType.INTEGER:
        return str(int(value))
    if column.data_type in _FLOAT_TYPES:
        return repr(float(value))
    if column.data_type is PlanDataType.DATE:
        return value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    text = value if isinstance(value, str) else str(value)
    # Escape anything that would otherwise be read back as the null sentinel.
    return f"{ESCAPE_PREFIX}{text}" if text.startswith(ESCAPE_PREFIX) else text


def decode_value(field: str, column: PlanColumn) -> Any:
    """Return the typed value for one CSV field."""

    if field == NULL_SENTINEL:
        return None
    if column.data_type is PlanDataType.BOOLEAN:
        return field == "true"
    if column.data_type is PlanDataType.INTEGER:
        return int(field)
    if column.data_type in _FLOAT_TYPES:
        return float(field)
    if column.data_type is PlanDataType.DATE:
        return date.fromisoformat(field)
    return field[1:] if field.startswith(ESCAPE_PREFIX) else field


_FLOAT_TYPES = frozenset(
    {
        PlanDataType.NUMBER,
        PlanDataType.DECIMAL,
        PlanDataType.CURRENCY,
        PlanDataType.PERCENTAGE,
    }
)


def encode_rows(frame: pl.DataFrame, columns: tuple[PlanColumn, ...]) -> bytes:
    """Return the gzipped canonical CSV for `frame`."""

    buffer = io.StringIO()
    writer = csv.writer(buffer, **_CSV_DIALECT)
    writer.writerow([column.key for column in columns])
    for row in frame.iter_rows(named=True):
        writer.writerow(
            [encode_value(row[column.key], column) for column in columns]
        )
    # mtime=0 keeps the gzip container byte-identical for identical input, so a
    # replay produces the same object rather than one that merely decodes the
    # same.
    return gzip.compress(buffer.getvalue().encode("utf-8"), mtime=0)


def decode_rows(
    payload: bytes,
    columns: tuple[PlanColumn, ...],
) -> list[dict[str, Any]]:
    """Return the typed rows encoded in `payload`."""

    text = gzip.decompress(payload).decode("utf-8")
    reader = csv.reader(io.StringIO(text), **_CSV_DIALECT)
    try:
        header = next(reader)
    except StopIteration:
        raise ResultSerializationError("result CSV has no header row") from None
    expected = [column.key for column in columns]
    if header != expected:
        raise ResultSerializationError(
            f"result CSV header {header} does not match the declared schema "
            f"{expected}"
        )
    rows: list[dict[str, Any]] = []
    for line in reader:
        if len(line) != len(columns):
            raise ResultSerializationError(
                f"result CSV row has {len(line)} fields, expected {len(columns)}"
            )
        rows.append(
            {
                column.key: decode_value(field, column)
                for column, field in zip(columns, line, strict=True)
            }
        )
    return rows


def build_schema_manifest(
    columns: tuple[PlanColumn, ...],
    *,
    row_count: int,
    content_hash: str,
) -> dict[str, Any]:
    """Return the manifest that makes the CSV readable without guessing."""

    return {
        "format_version": RESULT_FORMAT_VERSION,
        "encoding": {
            "charset": "utf-8",
            "compression": "gzip",
            "delimiter": _CSV_DIALECT["delimiter"],
            "quote_character": _CSV_DIALECT["quotechar"],
            "quote_escape": "double",
            "line_terminator": "\\n",
            "null_sentinel": NULL_SENTINEL,
            "escape_prefix": ESCAPE_PREFIX,
            "empty_string_is_not_null": semantics.EMPTY_STRING_IS_NOT_NULL,
        },
        "semantics": {
            "timezone": semantics.TIMEZONE,
            "locale": semantics.LOCALE,
            "date_format": semantics.DATE_INPUT_FORMAT,
            "rounding_mode": semantics.ROUNDING_MODE,
            "native_semantics_version": semantics.NATIVE_SEMANTICS_VERSION,
        },
        "row_count": row_count,
        "content_hash": content_hash,
        "columns": [
            {
                "key": column.key,
                "label": column.label,
                "data_type": column.data_type.value,
                "unit": column.unit,
                "nullable": column.nullable,
                "decimal_scale": (
                    semantics.DECIMAL_SCALE
                    if column.data_type in _FLOAT_TYPES
                    else None
                ),
            }
            for column in columns
        ],
    }


def encode_json(payload: dict[str, Any]) -> bytes:
    """Return canonical JSON bytes for a manifest."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


__all__ = [
    "ESCAPE_PREFIX",
    "NULL_SENTINEL",
    "RESULT_FORMAT_VERSION",
    "ResultSerializationError",
    "build_schema_manifest",
    "decode_rows",
    "decode_value",
    "encode_json",
    "encode_rows",
    "encode_value",
]
