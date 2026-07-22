from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, Mapping


RetrievalScopeValue = Literal["normal", "broad"]


@dataclass(frozen=True, slots=True)
class RetrievalLimits:
    text_per_query: int
    table_per_query: int
    text_candidates: int
    table_candidates: int
    final_text_chunks: int
    final_tables: int


RETRIEVAL_LIMITS: Final[Mapping[RetrievalScopeValue, RetrievalLimits]] = (
    MappingProxyType(
        {
            "normal": RetrievalLimits(
                text_per_query=10,
                table_per_query=8,
                text_candidates=20,
                table_candidates=12,
                final_text_chunks=6,
                final_tables=4,
            ),
            "broad": RetrievalLimits(
                text_per_query=15,
                table_per_query=12,
                text_candidates=35,
                table_candidates=25,
                final_text_chunks=10,
                final_tables=10,
            ),
        }
    )
)


def limits_for_scope(scope: str) -> RetrievalLimits:
    try:
        return RETRIEVAL_LIMITS[scope]  # type: ignore[index]
    except KeyError as exc:
        raise ValueError(f"Unsupported retrieval scope: {scope!r}") from exc
