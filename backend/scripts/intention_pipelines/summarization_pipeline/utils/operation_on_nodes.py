from collections import defaultdict
from typing import Any


def get_node_scope(
    nodes: list[dict[str, Any]],
    node_id: str | None = None,
) -> list[str] | None:
    """
    Returns:
        None:
            Summarize the whole document.
            Qdrant should filter only by doc_id.

        list[str]:
            Summarize a selected node.
            Includes the selected node and all its descendants.
    """

    # Scenario 1: Whole document
    if node_id is None:
        return None

    existing_node_ids = {
        node["node_id"]
        for node in nodes
    }

    if node_id not in existing_node_ids:
        raise ValueError(f"Node '{node_id}' was not found")

    # parent_id -> child node IDs
    children_by_parent = defaultdict(list)

    for node in nodes:
        parent_id = node.get("parent_id")

        if parent_id is not None:
            children_by_parent[parent_id].append(
                node["node_id"]
            )

    # Scenario 2: Selected section/chapter
    scope_node_ids = []
    visited = set()
    stack = [node_id]

    while stack:
        current_node_id = stack.pop()

        if current_node_id in visited:
            continue

        visited.add(current_node_id)
        scope_node_ids.append(current_node_id)

        children = children_by_parent.get(
            current_node_id,
            [],
        )

        stack.extend(children)

    return scope_node_ids



    