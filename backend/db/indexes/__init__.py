"""MongoDB index installers and read-only drift checks by domain."""

from .analysis import (
    ensure_analysis_indexes,
    migrate_analysis_indexes,
    verify_analysis_indexes,
)

__all__ = [
    "ensure_analysis_indexes",
    "migrate_analysis_indexes",
    "verify_analysis_indexes",
]
