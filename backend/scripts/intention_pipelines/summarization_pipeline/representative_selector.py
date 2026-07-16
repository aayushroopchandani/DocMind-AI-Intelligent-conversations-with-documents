from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Literal

import numpy as np
from sklearn.cluster import KMeans, MiniBatchKMeans


ClusterMethod = Literal[
    "all_chunks",
    "first_last_kmeans_mmr",
    "first_last_minibatch_kmeans_mmr",
]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


KEEP_ALL_MAX_CHUNKS = 8
KMEANS_MAX_CHUNKS = 200
REPRESENTATIVE_RATIO = min(
    1.0,
    max(0.01, _env_float("SUMMARY_REPRESENTATIVE_RATIO", 0.20)),
)
MIN_REPRESENTATIVE_CHUNKS = max(
    KEEP_ALL_MAX_CHUNKS,
    _env_int("SUMMARY_MIN_REPRESENTATIVE_CHUNKS", 8),
)
MAX_REPRESENTATIVE_CHUNKS = max(
    MIN_REPRESENTATIVE_CHUNKS,
    _env_int("SUMMARY_MAX_REPRESENTATIVE_CHUNKS", 80),
)
MMR_LAMBDA = min(1.0, max(0.0, _env_float("SUMMARY_MMR_LAMBDA", 0.65)))
RANDOM_STATE = 42


@dataclass(frozen=True, slots=True)
class RepresentativeSelection:
    indices: list[int]
    method: ClusterMethod
    cluster_count: int


def select_mmr_indices(
    embeddings: np.ndarray,
    budget: int,
    *,
    mandatory_indices: set[int] | None = None,
) -> list[int]:
    """Downselect existing candidates with MMR, without rerunning clustering."""
    chunk_count = len(embeddings)
    if chunk_count == 0 or budget <= 0:
        return []
    if chunk_count <= budget:
        return list(range(chunk_count))

    normalized = normalize_embeddings(embeddings)
    selected = {
        index
        for index in (mandatory_indices or set())
        if 0 <= index < chunk_count
    }

    if not selected:
        if budget == 1:
            center = normalized.mean(axis=0)
            center_norm = float(np.linalg.norm(center))
            if center_norm:
                center /= center_norm
            selected.add(int(np.argmax(normalized @ center)))
        else:
            selected.update({0, chunk_count - 1})

    # A caller may provide more mandatory positions than its final budget.
    # Preserve document boundaries deterministically in that unlikely case.
    if len(selected) > budget:
        ordered = sorted(selected)
        return [ordered[0], ordered[-1]][:budget]

    _fill_with_mmr(normalized, selected, budget)
    return sorted(selected)


def normalize_embeddings(embeddings: np.ndarray) -> np.ndarray:
    """L2-normalize dense chunk embeddings before clustering and MMR."""
    matrix = np.asarray(embeddings, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
        raise ValueError("embeddings must be a non-empty two-dimensional matrix")
    if not np.isfinite(matrix).all():
        raise ValueError("embeddings contain non-finite values")

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("embeddings must not contain zero-length vectors")
    return matrix / norms


def _representative_budget(chunk_count: int) -> int:
    requested = max(
        MIN_REPRESENTATIVE_CHUNKS,
        math.ceil(chunk_count * REPRESENTATIVE_RATIO),
    )
    return min(chunk_count, MAX_REPRESENTATIVE_CHUNKS, requested)


def _cluster_count(chunk_count: int, budget: int) -> int:
    # Leave room for mandatory first/last chunks and at least one MMR-selected
    # chunk while keeping enough clusters to cover the main semantic regions.
    return max(1, min(round(math.sqrt(chunk_count)), budget - 3))


def _fit_clusters(
    embeddings: np.ndarray,
    cluster_count: int,
    *,
    use_minibatch: bool,
) -> tuple[np.ndarray, np.ndarray]:
    if use_minibatch:
        estimator = MiniBatchKMeans(
            n_clusters=cluster_count,
            batch_size=min(len(embeddings), max(256, cluster_count * 10)),
            n_init=3,
            random_state=RANDOM_STATE,
            reassignment_ratio=0.01,
        )
    else:
        estimator = KMeans(
            n_clusters=cluster_count,
            n_init=10,
            random_state=RANDOM_STATE,
        )

    labels = estimator.fit_predict(embeddings)
    centers = np.asarray(estimator.cluster_centers_, dtype=np.float32)
    return labels, centers


def _nearest_centroid_indices(
    embeddings: np.ndarray,
    labels: np.ndarray,
    centers: np.ndarray,
) -> list[int]:
    selected: list[int] = []
    for cluster_id, center in enumerate(centers):
        member_indices = np.flatnonzero(labels == cluster_id)
        if member_indices.size == 0:
            continue
        similarities = embeddings[member_indices] @ center
        selected.append(int(member_indices[int(np.argmax(similarities))]))
    return selected


def _fill_with_mmr(
    embeddings: np.ndarray,
    selected: set[int],
    budget: int,
) -> None:
    if len(selected) >= budget:
        return

    document_center = embeddings.mean(axis=0)
    center_norm = float(np.linalg.norm(document_center))
    if center_norm:
        document_center /= center_norm
    relevance = embeddings @ document_center

    candidates = np.array(
        [index for index in range(len(embeddings)) if index not in selected],
        dtype=np.int64,
    )
    if candidates.size == 0:
        return

    selected_array = np.fromiter(selected, dtype=np.int64)
    max_similarity = np.max(
        embeddings[candidates] @ embeddings[selected_array].T,
        axis=1,
    )

    while len(selected) < budget and candidates.size:
        scores = (
            MMR_LAMBDA * relevance[candidates]
            - (1.0 - MMR_LAMBDA) * max_similarity
        )
        best_position = int(np.argmax(scores))
        best_index = int(candidates[best_position])
        selected.add(best_index)

        keep_mask = np.ones(candidates.size, dtype=bool)
        keep_mask[best_position] = False
        candidates = candidates[keep_mask]
        max_similarity = max_similarity[keep_mask]
        if candidates.size:
            max_similarity = np.maximum(
                max_similarity,
                embeddings[candidates] @ embeddings[best_index],
            )


def select_representative_indices(
    embeddings: np.ndarray,
) -> RepresentativeSelection:
    """
    Select a bounded, ordered set of representative chunks for one node.

    The input rows must already follow original document order. First and last
    chunks are mandatory, centroid-nearest chunks provide coverage, and MMR
    fills the remaining budget with semantically diverse chunks.
    """
    chunk_count = len(embeddings)
    if chunk_count == 0:
        return RepresentativeSelection([], "all_chunks", 0)
    if chunk_count <= KEEP_ALL_MAX_CHUNKS:
        return RepresentativeSelection(
            indices=list(range(chunk_count)),
            method="all_chunks",
            cluster_count=0,
        )

    normalized = normalize_embeddings(embeddings)
    budget = _representative_budget(chunk_count)
    cluster_count = _cluster_count(chunk_count, budget)
    use_minibatch = chunk_count > KMEANS_MAX_CHUNKS
    labels, centers = _fit_clusters(
        normalized,
        cluster_count,
        use_minibatch=use_minibatch,
    )

    selected = {0, chunk_count - 1}
    selected.update(_nearest_centroid_indices(normalized, labels, centers))
    _fill_with_mmr(normalized, selected, budget)

    return RepresentativeSelection(
        indices=sorted(selected),
        method=(
            "first_last_minibatch_kmeans_mmr"
            if use_minibatch
            else "first_last_kmeans_mmr"
        ),
        cluster_count=cluster_count,
    )
