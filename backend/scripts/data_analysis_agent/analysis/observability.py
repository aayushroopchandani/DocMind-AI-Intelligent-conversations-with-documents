from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

try:
    from langsmith.run_helpers import get_current_run_tree as _get_current_run_tree
except ImportError:  # pragma: no cover - LangChain normally installs LangSmith
    _get_current_run_tree = None


TraceScalar = str | int | float | bool | None


def _trace_value(value: Any) -> TraceScalar | list[TraceScalar]:
    if isinstance(value, Enum):
        return str(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        output: list[TraceScalar] = []
        for item in value[:20]:
            normalized = _trace_value(item)
            if isinstance(normalized, list):
                output.extend(normalized)
            else:
                output.append(normalized)
        return output
    return str(value)


def _root_run(run: Any) -> Any:
    current = run
    seen: set[int] = set()
    while getattr(current, "parent_run", None) is not None:
        identity = id(current)
        if identity in seen:
            break
        seen.add(identity)
        current = current.parent_run
    return current


def record_analysis_trace(
    *,
    metrics: Mapping[str, Any],
    tags: Sequence[str] = (),
    propagate_to_root: bool = True,
) -> bool:
    """Add bounded analysis metrics to active LangSmith node and root traces."""

    if _get_current_run_tree is None:
        return False
    try:
        run = _get_current_run_tree()
    except Exception:
        return False
    if run is None:
        return False

    metadata = {
        str(key): _trace_value(value)
        for key, value in metrics.items()
        if value is not None
    }
    normalized_tags = tuple(
        dict.fromkeys(
            str(tag).strip()
            for tag in tags
            if str(tag).strip()
        )
    )[:20]
    targets = [run]
    if propagate_to_root:
        root = _root_run(run)
        if root is not run:
            targets.append(root)

    try:
        for target in targets:
            if metadata:
                target.add_metadata(metadata)
            if normalized_tags:
                target.add_tags(normalized_tags)
    except Exception:
        return False
    return True
