"""The native operation registry.

Importing this package registers every executable operation. Modules are grouped
by what they do to a table — rows, columns, groups, joins — which keeps each one
small enough to read alongside the 9.5 rules it implements.

`compose_response` and `generate_dataset` are deliberately absent; see
`runtime/execution/contracts.py` for why.
"""

from __future__ import annotations

from . import columns, grouping, joining, rows  # noqa: F401  (registration)
from .base import (
    NativeExecutionSemanticError,
    Operation,
    UnsupportedOperationError,
    lookup,
    registered_kinds,
    require_columns,
)
from .grouping import discover_categories
from .joining import check_expansion, expansion_ratio

__all__ = [
    "NativeExecutionSemanticError",
    "Operation",
    "UnsupportedOperationError",
    "check_expansion",
    "discover_categories",
    "expansion_ratio",
    "lookup",
    "registered_kinds",
    "require_columns",
]
