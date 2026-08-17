"""Result validation, lineage, previews and durable artifacts (Phase 9.9).

A native execution produces a frame; this package turns it into something that
can be published, audited and replayed. The bundle is deliberately four files —
rows, schema, lineage, preview — because CSV alone cannot describe its own
types, and a result nobody can read back is not durable.
"""

from .lineage import LINEAGE_FORMAT_VERSION, build_lineage
from .previews import MAX_PREVIEW_ROWS, build_preview
from .publisher import (
    PublishedBundle,
    ResultPublicationError,
    object_key,
    publish_result,
    verify_bundle,
)
from .serialization import (
    NULL_SENTINEL,
    RESULT_FORMAT_VERSION,
    ResultSerializationError,
    build_schema_manifest,
    decode_rows,
    encode_json,
    encode_rows,
)
from .validation import ResultIssue, validate_result

__all__ = [
    "LINEAGE_FORMAT_VERSION",
    "MAX_PREVIEW_ROWS",
    "NULL_SENTINEL",
    "RESULT_FORMAT_VERSION",
    "PublishedBundle",
    "ResultIssue",
    "ResultPublicationError",
    "ResultSerializationError",
    "build_lineage",
    "build_preview",
    "build_schema_manifest",
    "decode_rows",
    "encode_json",
    "encode_rows",
    "object_key",
    "publish_result",
    "validate_result",
    "verify_bundle",
]
