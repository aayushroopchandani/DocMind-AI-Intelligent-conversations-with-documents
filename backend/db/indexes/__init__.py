"""MongoDB index installers grouped by application domain."""

from .analysis import ensure_analysis_indexes

__all__ = ["ensure_analysis_indexes"]
