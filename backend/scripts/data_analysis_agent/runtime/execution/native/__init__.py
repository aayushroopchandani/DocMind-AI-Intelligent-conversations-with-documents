"""Native execution engine: typed plan steps compiled onto pinned Polars.

This package is deliberately empty at import time. Importing the engine costs a
Polars import, and the modules that only need the semantic policy — execution
keys, admission — must not pay it. Import `native.engine`, `native.staging` or
`native.semantics` directly instead of relying on package-level re-exports.
"""

from __future__ import annotations

__all__: list[str] = []
