from __future__ import annotations

from collections.abc import Sequence


def combine_queries(
    original_query: str,
    shared_queries: Sequence[str],
    specialized_queries: Sequence[str],
) -> list[str]:
    """Return original, shared, then specialized queries without duplicates."""

    output: list[str] = []
    seen: set[str] = set()
    for value in (original_query, *shared_queries, *specialized_queries):
        query = " ".join(str(value or "").split()).strip(" .")
        canonical = query.casefold()
        if query and canonical not in seen:
            seen.add(canonical)
            output.append(query)
    if not output:
        raise ValueError("At least one retrieval query is required")
    return output
