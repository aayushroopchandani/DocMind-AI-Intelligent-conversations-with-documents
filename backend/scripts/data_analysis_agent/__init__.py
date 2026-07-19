"""PDF ingestion primitives for the data-analysis agent."""

from typing import Any

__all__ = ["extract_tables_from_pdf"]


def __getattr__(name: str) -> Any:
    # Keep the package import lightweight for the isolated Docling interpreter,
    # which executes docling_worker without installing the FastAPI stack.
    if name == "extract_tables_from_pdf":
        from .table_extractor import extract_tables_from_pdf

        return extract_tables_from_pdf
    raise AttributeError(name)
