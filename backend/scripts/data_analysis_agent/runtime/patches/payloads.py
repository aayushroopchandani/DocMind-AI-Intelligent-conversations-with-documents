"""Building a patch payload without ever holding two copies (Phase 9.10.3).

A payload is the grid a `write_range` operation puts on the sheet. Small ones
travel inside the patch; large ones are uploaded as immutable row blocks and the
patch carries only their checksums, because 9.9.4 keeps bulk data out of
MongoDB and the browser must not have to build several full copies to apply one
result.

Everything here is one pass. Rows arrive from the grid, feed the range hash and
the current chunk buffer, and are dropped. The `expected_after_hash` the patch
commits to therefore falls out of the same traversal that produced the bytes —
there is no second walk over the result to hash what was just written, and no
moment where the whole grid exists as a Python object *and* as JSON.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from .cells import CellState, RangeHashBuilder, canonical_cell_payload
from .operations import (
    MAX_INLINE_CELLS,
    ChunkedPayload,
    InlinePayload,
    PatchPayload,
    PayloadChunkReference,
)


PAYLOAD_SCHEMA_VERSION = "1.0"

MAX_CHUNK_CELLS = 20_000
"""Roughly a megabyte of encoded cells per block."""

MAX_PAYLOAD_CHUNKS = 1_000
"""Matches the protocol's chunk limit; a larger result belongs in a file."""

DEFAULT_HEAD_ROWS = 21
"""Rows kept aside for the proposal preview — a header plus twenty.

Captured during the one pass that builds the payload. Re-reading a chunk back
out of blob storage to show the user four rows would be absurd.
"""


class PayloadWriter(Protocol):
    """Stores one immutable chunk and returns the key it can be fetched by.

    Deliberately narrow: the compiler needs somewhere to put bytes, not a blob
    store. Signed delivery URLs are minted at download time and never persisted
    in the patch (9.10.3).
    """

    async def write_chunk(self, *, index: int, data: bytes, sha256: str) -> str: ...


class PayloadTooLargeError(ValueError):
    """The grid needs more chunks than the protocol allows."""


class PayloadStorageRequiredError(ValueError):
    """A grid past the inline limit was compiled with nowhere to store it."""


@dataclass(frozen=True, slots=True)
class BuiltPayload:
    """The payload, the hash of the grid it produces, and a preview head."""

    payload: PatchPayload
    after_hash: str
    cell_count: int
    byte_count: int
    head: tuple[tuple[CellState, ...], ...] = ()

    @property
    def is_inline(self) -> bool:
        return isinstance(self.payload, InlinePayload)


def encode_chunk(
    *,
    first_row: int,
    last_row: int,
    columns: int,
    rows: Iterable[tuple[CellState, ...]],
) -> bytes:
    """Return one row block in the canonical chunk encoding.

    The same cell representation the hash uses, so the browser decodes chunks
    with the code it already has for verifying guards.
    """

    return json.dumps(
        {
            "schema_version": PAYLOAD_SCHEMA_VERSION,
            "first_row": first_row,
            "last_row": last_row,
            "columns": columns,
            "cells": [
                [canonical_cell_payload(cell) for cell in row] for row in rows
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


async def build_payload(
    grid: Iterable[tuple[CellState, ...]],
    *,
    range_a1: str,
    rows: int,
    columns: int,
    writer: PayloadWriter | None = None,
    max_inline_cells: int = MAX_INLINE_CELLS,
    max_chunk_cells: int = MAX_CHUNK_CELLS,
    head_rows: int = DEFAULT_HEAD_ROWS,
) -> BuiltPayload:
    """Return the payload for `grid`, inline or chunked, and its result hash."""

    builder = RangeHashBuilder(range_a1)
    if (builder.rows, builder.columns) != (rows, columns):
        raise ValueError(
            f"{range_a1} is {builder.rows}x{builder.columns}; the grid is "
            f"{rows}x{columns}"
        )
    if rows * columns <= max_inline_cells:
        return _inline(
            grid,
            builder=builder,
            rows=rows,
            columns=columns,
            head_rows=head_rows,
        )
    if writer is None:
        raise PayloadStorageRequiredError(
            f"a {rows * columns}-cell payload exceeds the {max_inline_cells}-"
            "cell inline limit and needs blob storage"
        )
    return await _chunked(
        grid,
        builder=builder,
        rows=rows,
        columns=columns,
        writer=writer,
        max_chunk_cells=max_chunk_cells,
        head_rows=head_rows,
    )


def _inline(
    grid: Iterable[tuple[CellState, ...]],
    *,
    builder: RangeHashBuilder,
    rows: int,
    columns: int,
    head_rows: int,
) -> BuiltPayload:
    materialized: list[tuple[CellState, ...]] = []
    for row in grid:
        builder.add_row(row)
        materialized.append(row)
    return BuiltPayload(
        payload=InlinePayload(cells=tuple(materialized)),
        after_hash=builder.digest(),
        cell_count=rows * columns,
        byte_count=0,
        head=tuple(materialized[:head_rows]),
    )


async def _chunked(
    grid: Iterable[tuple[CellState, ...]],
    *,
    builder: RangeHashBuilder,
    rows: int,
    columns: int,
    writer: PayloadWriter,
    max_chunk_cells: int,
    head_rows: int,
) -> BuiltPayload:
    rows_per_chunk = max(1, max_chunk_cells // columns)
    expected_chunks = -(-rows // rows_per_chunk)
    if expected_chunks > MAX_PAYLOAD_CHUNKS:
        raise PayloadTooLargeError(
            f"a {rows}-row payload needs {expected_chunks} chunks; the "
            f"protocol allows {MAX_PAYLOAD_CHUNKS}"
        )

    references: list[PayloadChunkReference] = []
    head: list[tuple[CellState, ...]] = []
    buffer: list[tuple[CellState, ...]] = []
    first_row = 0
    total_bytes = 0
    emitted = 0

    async def flush() -> None:
        nonlocal buffer, first_row, total_bytes
        if not buffer:
            return
        last_row = first_row + len(buffer) - 1
        data = encode_chunk(
            first_row=first_row,
            last_row=last_row,
            columns=columns,
            rows=buffer,
        )
        digest = hashlib.sha256(data).hexdigest()
        object_key = await writer.write_chunk(
            index=len(references),
            data=data,
            sha256=digest,
        )
        references.append(
            PayloadChunkReference(
                index=len(references),
                first_row=first_row,
                last_row=last_row,
                byte_count=len(data),
                sha256=digest,
                object_key=object_key,
            )
        )
        total_bytes += len(data)
        first_row = last_row + 1
        buffer = []

    for row in grid:
        builder.add_row(row)
        buffer.append(row)
        if len(head) < head_rows:
            head.append(row)
        emitted += 1
        if len(buffer) >= rows_per_chunk:
            await flush()
    await flush()

    if emitted != rows:
        raise ValueError(
            f"grid produced {emitted} rows; {rows} were expected"
        )
    return BuiltPayload(
        payload=ChunkedPayload(
            chunks=tuple(references),
            total_rows=rows,
            total_columns=columns,
        ),
        after_hash=builder.digest(),
        cell_count=rows * columns,
        byte_count=total_bytes,
        head=tuple(head),
    )


__all__ = [
    "DEFAULT_HEAD_ROWS",
    "MAX_CHUNK_CELLS",
    "MAX_PAYLOAD_CHUNKS",
    "PAYLOAD_SCHEMA_VERSION",
    "BuiltPayload",
    "PayloadStorageRequiredError",
    "PayloadTooLargeError",
    "PayloadWriter",
    "build_payload",
    "encode_chunk",
]
