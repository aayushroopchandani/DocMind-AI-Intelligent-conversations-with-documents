from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from db.models.structured_table import StructuredTable


class _HashWriter(Protocol):
    def update(self, data: bytes, /) -> None: ...

    def hexdigest(self) -> str: ...


def _update_hash(digest: _HashWriter, label: str, value: Any) -> None:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    digest.update(label.encode("ascii"))
    digest.update(len(encoded).to_bytes(8, byteorder="big", signed=False))
    digest.update(encoded)


def source_version(table: StructuredTable) -> str:
    """Incrementally hash source content without copying the complete table."""

    digest = hashlib.sha256()
    _update_hash(
        digest,
        "metadata",
        {
            "table_id": table.table_id,
            "document_id": table.document_id,
            "title": table.title,
            "extraction_method": table.extraction_method,
            "page_start": table.page_start,
            "page_end": table.page_end,
        },
    )
    for column in table.columns:
        _update_hash(digest, "column", column.model_dump(mode="json"))
    for row in table.rows:
        _update_hash(digest, "row", row)
    for fragment in table.source_fragments:
        _update_hash(digest, "source", fragment.model_dump(mode="json"))
    return digest.hexdigest()


def raw_dataset_id(table: StructuredTable, version: str) -> str:
    identity = f"docmind:dataset:raw:{table.user_id}:{table.table_id}:{version}"
    return str(uuid5(NAMESPACE_URL, identity))
