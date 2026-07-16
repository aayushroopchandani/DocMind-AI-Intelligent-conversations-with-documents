from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np

from .representative_selector import select_mmr_indices


@dataclass(frozen=True, slots=True)
class ScopeSelection:
    chunks: list[dict[str, Any]]
    candidate_count: int
    node_quotas: dict[str, int]


@dataclass(frozen=True, slots=True)
class SectionAwareGroup:
    label: str
    chunks: list[dict[str, Any]]


def _node_source_counts(nodes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in nodes:
        node_id = node.get("node_id")
        if node_id is None:
            continue
        summary_index = node.get("summary_index") or {}
        try:
            count = int(summary_index.get("chunk_count", 0))
        except (TypeError, ValueError):
            count = 0
        counts[str(node_id)] = max(0, count)
    return counts


def _evenly_spaced_indices(length: int, limit: int) -> list[int]:
    if limit >= length:
        return list(range(length))
    if limit <= 1:
        return [length // 2]
    return sorted(
        {
            round((length - 1) * offset / (limit - 1))
            for offset in range(limit)
        }
    )


def _allocate_node_quotas(
    *,
    chunks_by_node: dict[str, list[dict[str, Any]]],
    source_counts: dict[str, int],
    budget: int,
) -> dict[str, int]:
    ordered_node_ids = list(chunks_by_node)
    node_positions = {
        node_id: position for position, node_id in enumerate(ordered_node_ids)
    }
    if not ordered_node_ids or budget <= 0:
        return {}

    if len(ordered_node_ids) <= budget:
        covered_node_ids = ordered_node_ids
    else:
        covered_node_ids = [
            ordered_node_ids[index]
            for index in _evenly_spaced_indices(len(ordered_node_ids), budget)
        ]

    quotas = {node_id: 1 for node_id in covered_node_ids}
    remaining = budget - len(quotas)
    weights = {
        node_id: math.sqrt(
            max(source_counts.get(node_id, 0), len(chunks_by_node[node_id]), 1)
        )
        for node_id in covered_node_ids
    }

    # Weighted round-robin with diminishing returns is deterministic, fair to
    # small nodes, and prevents one large section from consuming the scope.
    while remaining > 0:
        eligible = [
            node_id
            for node_id in covered_node_ids
            if quotas[node_id] < len(chunks_by_node[node_id])
        ]
        if not eligible:
            break
        chosen = max(
            eligible,
            key=lambda node_id: (
                weights[node_id] / (quotas[node_id] + 1),
                -node_positions[node_id],
            ),
        )
        quotas[chosen] += 1
        remaining -= 1

    return quotas


def select_scope_representatives(
    *,
    chunks: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    budget: int,
) -> ScopeSelection:
    """Apply a fair scope-level budget to precomputed per-node candidates."""
    candidate_count = len(chunks)
    if candidate_count <= budget:
        return ScopeSelection(
            chunks=chunks,
            candidate_count=candidate_count,
            node_quotas={},
        )

    chunks_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        metadata = chunk.get("metadata") or {}
        node_id = str(metadata.get("node_id") or "__unassigned__")
        chunks_by_node[node_id].append(chunk)

    quotas = _allocate_node_quotas(
        chunks_by_node=chunks_by_node,
        source_counts=_node_source_counts(nodes),
        budget=budget,
    )
    first_chunk_id = str(chunks[0].get("id"))
    last_chunk_id = str(chunks[-1].get("id"))
    selected_ids: set[str] = set()

    for node_id, node_chunks in chunks_by_node.items():
        quota = quotas.get(node_id, 0)
        if quota <= 0:
            continue

        mandatory: set[int] = set()
        for index, chunk in enumerate(node_chunks):
            chunk_id = str(chunk.get("id"))
            if chunk_id in {first_chunk_id, last_chunk_id}:
                mandatory.add(index)

        if len(mandatory) > quota:
            quotas[node_id] = len(mandatory)
            quota = len(mandatory)

        if quota >= 2 and len(node_chunks) >= 2:
            mandatory.update({0, len(node_chunks) - 1})

        if quota >= len(node_chunks):
            chosen_indices = list(range(len(node_chunks)))
        else:
            try:
                embeddings = np.asarray(
                    [chunk["embedding"] for chunk in node_chunks],
                    dtype=np.float32,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "Scope budgeting requires representative chunk embeddings"
                ) from exc
            chosen_indices = select_mmr_indices(
                embeddings,
                quota,
                mandatory_indices=mandatory or None,
            )

        selected_ids.update(str(node_chunks[index].get("id")) for index in chosen_indices)

    # Mandatory scope boundaries can only exceed the configured budget when
    # the budget is one. Normal configuration always uses a much larger value.
    selected_ids.update({first_chunk_id, last_chunk_id})
    selected_chunks = [
        chunk for chunk in chunks if str(chunk.get("id")) in selected_ids
    ]
    return ScopeSelection(
        chunks=selected_chunks,
        candidate_count=candidate_count,
        node_quotas=quotas,
    )


def _node_path(
    node_id: str,
    nodes_by_id: dict[str, dict[str, Any]],
) -> str:
    titles: list[str] = []
    current_id: str | None = node_id
    seen: set[str] = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        node = nodes_by_id.get(current_id)
        if node is None:
            break
        title = str(node.get("title") or "").strip()
        if title:
            titles.append(title)
        parent_id = node.get("parent_id")
        current_id = str(parent_id) if parent_id else None
    return " > ".join(reversed(titles)) or node_id


def _scope_anchor(
    node_id: str,
    scope_root_node_id: str | None,
    nodes_by_id: dict[str, dict[str, Any]],
) -> str:
    if scope_root_node_id is None:
        current_id = node_id
        seen: set[str] = set()
        while current_id not in seen:
            seen.add(current_id)
            node = nodes_by_id.get(current_id)
            if node is None or not node.get("parent_id"):
                return current_id
            current_id = str(node["parent_id"])
        return node_id

    if node_id == scope_root_node_id:
        return node_id
    current_id = node_id
    seen: set[str] = set()
    while current_id not in seen:
        seen.add(current_id)
        node = nodes_by_id.get(current_id)
        if node is None:
            return node_id
        parent_id = str(node.get("parent_id") or "")
        if parent_id == scope_root_node_id:
            return current_id
        if not parent_id:
            return node_id
        current_id = parent_id
    return node_id


def _group_label(
    chunks: list[dict[str, Any]],
    nodes_by_id: dict[str, dict[str, Any]],
) -> str:
    node_ids = list(
        dict.fromkeys(
            str((chunk.get("metadata") or {}).get("node_id") or "unknown")
            for chunk in chunks
        )
    )
    paths = [_node_path(node_id, nodes_by_id) for node_id in node_ids]
    if len(paths) <= 2:
        return " | ".join(paths)
    return f"{paths[0]} | ... | {paths[-1]}"


def build_section_aware_groups(
    *,
    chunks: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    scope_root_node_id: str | None,
    group_size: int,
    max_groups: int,
) -> list[SectionAwareGroup]:
    """Pack ordered chunks around outline boundaries with a hard group bound."""
    if not chunks:
        return []
    nodes_by_id = {
        str(node["node_id"]): node
        for node in nodes
        if node.get("node_id") is not None
    }

    blocks: list[tuple[str, list[dict[str, Any]]]] = []
    current_anchor: str | None = None
    for chunk in chunks:
        node_id = str((chunk.get("metadata") or {}).get("node_id") or "unknown")
        anchor = _scope_anchor(node_id, scope_root_node_id, nodes_by_id)
        if anchor != current_anchor:
            anchor_node = nodes_by_id.get(anchor) or {}
            parent_key = str(anchor_node.get("parent_id") or "__root__")
            blocks.append((parent_key, []))
            current_anchor = anchor
        blocks[-1][1].append(chunk)

    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for parent_key, block in blocks:
        groups.extend(
            (parent_key, block[index : index + group_size])
            for index in range(0, len(block), group_size)
        )

    while len(groups) > max_groups:
        mergeable = [
            (len(groups[index][1]) + len(groups[index + 1][1]), index)
            for index in range(len(groups) - 1)
            if (
                groups[index][0] == groups[index + 1][0]
                and len(groups[index][1]) + len(groups[index + 1][1])
                <= group_size
            )
        ]
        if not mergeable:
            break
        _, index = min(mergeable)
        parent_key = groups[index][0]
        groups[index : index + 2] = [
            (parent_key, groups[index][1] + groups[index + 1][1])
        ]

    if len(groups) > max_groups:
        # Rebalance only contiguous sibling runs. This may move a split point
        # inside a small section, but never mixes sections with different
        # parents and achieves the minimum possible calls for each run.
        rebalanced: list[tuple[str, list[dict[str, Any]]]] = []
        run_start = 0
        while run_start < len(groups):
            parent_key = groups[run_start][0]
            run_end = run_start + 1
            while run_end < len(groups) and groups[run_end][0] == parent_key:
                run_end += 1
            run_chunks = [
                chunk
                for _, group in groups[run_start:run_end]
                for chunk in group
            ]
            required = math.ceil(len(run_chunks) / group_size)
            balanced_size = math.ceil(len(run_chunks) / required)
            rebalanced.extend(
                (parent_key, run_chunks[index : index + balanced_size])
                for index in range(0, len(run_chunks), balanced_size)
            )
            run_start = run_end
        groups = rebalanced

    if len(groups) > max_groups:
        # A malformed/disconnected outline can make the strict parent rule
        # incompatible with the hard call bound. Preserve document order and
        # the hard bound as a final defensive fallback.
        required_groups = math.ceil(len(chunks) / group_size)
        target_groups = max(required_groups, max_groups)
        balanced_size = math.ceil(len(chunks) / target_groups)
        groups = [
            ("__fallback__", chunks[index : index + balanced_size])
            for index in range(0, len(chunks), balanced_size)
        ]

    return [
        SectionAwareGroup(
            label=_group_label(group, nodes_by_id),
            chunks=group,
        )
        for _, group in groups
    ]
