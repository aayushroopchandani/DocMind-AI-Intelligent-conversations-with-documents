from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from .relevance import normalize_text


Candidate = dict[str, Any]
_T = TypeVar("_T")


def _tokens(value: Any) -> list[str]:
    return normalize_text(value).split()


def _token_set(value: Any) -> set[str]:
    return set(_tokens(value))


def _shingles(value: Any, size: int = 3) -> set[tuple[str, ...]]:
    tokens = _tokens(value)
    if len(tokens) < size:
        return {tuple(tokens)} if tokens else set()
    return {
        tuple(tokens[index : index + size])
        for index in range(len(tokens) - size + 1)
    }


def _jaccard(left: set[Any], right: set[Any]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _relative_candidates(
    candidates: Sequence[Candidate],
    *,
    minimum_ratio: float = 0.55,
    minimum_score: float = 0.0,
    ensure_at_least: int = 0,
) -> list[Candidate]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            -float(item.get("relevance_score") or 0.0),
            str(item.get("chunk_id") or item.get("table_id") or ""),
        ),
    )
    if not ordered:
        return []
    best_score = float(ordered[0].get("relevance_score") or 0.0)
    if best_score <= 0:
        return ordered[:ensure_at_least]
    selected = [
        item
        for item in ordered
        if (
            float(item.get("relevance_score") or 0.0) >= minimum_score
            and float(item.get("relevance_score") or 0.0)
            >= best_score * minimum_ratio
        )
    ]
    if len(selected) < ensure_at_least:
        return ordered[:ensure_at_least]
    return selected


def _round_robin_documents(
    candidates: Sequence[_T],
    *,
    document_key: Callable[[_T], str],
    limit: int,
) -> list[_T]:
    buckets: dict[str, list[_T]] = defaultdict(list)
    order: list[str] = []
    for index, candidate in enumerate(candidates):
        document_id = document_key(candidate) or f"__unknown_{index}"
        if document_id not in buckets:
            order.append(document_id)
        buckets[document_id].append(candidate)

    selected: list[_T] = []
    offset = 0
    while len(selected) < limit:
        added = False
        for document_id in order:
            bucket = buckets[document_id]
            if offset < len(bucket):
                selected.append(bucket[offset])
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
        offset += 1
    return selected


def select_text_chunks(
    candidates: Sequence[Candidate],
    *,
    limit: int,
    broad: bool,
    duplicate_threshold: float = 0.82,
    max_per_page: int = 2,
    minimum_score: float = 0.0,
    minimum_ratio: float = 0.55,
) -> list[Candidate]:
    unique: list[Candidate] = []
    unique_shingles: list[tuple[str, set[tuple[str, ...]]]] = []
    seen_ids: set[str] = set()
    page_counts: dict[tuple[str, str], int] = defaultdict(int)
    for candidate in _relative_candidates(
        candidates,
        minimum_score=minimum_score,
        minimum_ratio=minimum_ratio,
        ensure_at_least=1,
    ):
        chunk_id = str(candidate.get("chunk_id") or "")
        if chunk_id and chunk_id in seen_ids:
            continue
        metadata = candidate.get("metadata") or {}
        document_id = str(metadata.get("doc_id") or "")
        text_shingles = _shingles(candidate.get("text"))
        if text_shingles and any(
            document_id == existing_document_id
            and _jaccard(text_shingles, existing_shingles) >= duplicate_threshold
            for existing_document_id, existing_shingles in unique_shingles
        ):
            continue

        page = str(metadata.get("page_number") or metadata.get("page") or "")
        page_key = (document_id, page)
        if page and page_counts[page_key] >= max_per_page:
            continue
        if chunk_id:
            seen_ids.add(chunk_id)
        if page:
            page_counts[page_key] += 1
        unique.append(candidate)
        unique_shingles.append((document_id, text_shingles))

    if broad:
        return _round_robin_documents(
            unique,
            document_key=lambda item: str(
                (item.get("metadata") or {}).get("doc_id") or ""
            ),
            limit=limit,
        )
    return unique[:limit]


def _pages_overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        left_start = int(left.get("page_start"))
        left_end = int(left.get("page_end"))
        right_start = int(right.get("page_start"))
        right_end = int(right.get("page_end"))
    except (TypeError, ValueError):
        return False
    return max(left_start, right_start) <= min(left_end, right_end)


def _duplicate_table(left: Candidate, right: Candidate) -> bool:
    if str(left.get("document_id") or "") != str(right.get("document_id") or ""):
        return False
    if not _pages_overlap(left, right):
        return False
    left_columns = _token_set(" ".join(left.get("columns") or []))
    right_columns = _token_set(" ".join(right.get("columns") or []))
    title_similarity = _jaccard(
        _token_set(left.get("title")),
        _token_set(right.get("title")),
    )
    summary_similarity = _jaccard(
        _token_set(left.get("summary")),
        _token_set(right.get("summary")),
    )
    column_similarity = _jaccard(left_columns, right_columns)
    return column_similarity >= 0.80 and (
        title_similarity >= 0.75 or summary_similarity >= 0.85
    )


def select_tables(
    candidates: Sequence[Candidate],
    *,
    limit: int,
    broad: bool,
    minimum_score: float = 0.0,
    minimum_ratio: float = 0.55,
) -> list[Candidate]:
    unique: list[Candidate] = []
    seen_ids: set[str] = set()
    for candidate in _relative_candidates(
        candidates,
        minimum_score=minimum_score,
        minimum_ratio=minimum_ratio,
    ):
        table_id = str(
            candidate.get("table_id") or candidate.get("point_id") or ""
        )
        if table_id and table_id in seen_ids:
            continue
        if any(_duplicate_table(candidate, existing) for existing in unique):
            continue
        if table_id:
            seen_ids.add(table_id)
        unique.append(candidate)

    if broad:
        return _round_robin_documents(
            unique,
            document_key=lambda item: str(item.get("document_id") or ""),
            limit=limit,
        )
    return unique[:limit]
