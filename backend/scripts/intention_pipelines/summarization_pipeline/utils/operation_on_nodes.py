from collections import defaultdict
from typing import Any


def get_node_scope(
    nodes: list[dict[str, Any]],
    node_id: str | None = None,
) -> list[str] | None:
    """
    Return the selected node and all of its descendants in document order.

    Returns:
        None:
            Summarize the whole document. Filter chunks by document_id only.

        list[str]:
            Selected node_id and all descendant node IDs.
    """
    if node_id is None:
        return None

    node_by_id: dict[str, dict[str, Any]] = {}
    children_by_parent: dict[str, list[str]] = defaultdict(list)

    for node in nodes:
        current_id = node.get("node_id")

        if not current_id:
            continue

        if current_id in node_by_id:
            raise ValueError(f"Duplicate node_id found: '{current_id}'")

        node_by_id[current_id] = node

        parent_id = node.get("parent_id")
        if parent_id:
            children_by_parent[parent_id].append(current_id)

    if node_id not in node_by_id:
        raise ValueError(f"Node '{node_id}' was not found")

    scope_ids: list[str] = []
    visited: set[str] = set()
    stack: list[str] = [node_id]

    while stack:
        current_id = stack.pop()

        if current_id in visited:
            continue

        visited.add(current_id)
        scope_ids.append(current_id)

        stack.extend(children_by_parent.get(current_id, []))

    scope_ids.sort(
        key=lambda current_id: (
            node_by_id[current_id].get("page_start", float("inf")),
            node_by_id[current_id].get("order", float("inf")),
            node_by_id[current_id].get("page_end", float("inf")),
        )
    )

    return scope_ids