"""Deterministic execution-semantic projection used by plan hashing.

Phase 9.2.1 requires ``plan_hash`` to cover execution semantics while ignoring
presentation text. The projection therefore has to distinguish two things that
look identical once a model has been dumped to plain JSON:

* a *model field* that exists only for display (``PlanColumn.label``);
* an opaque ``JsonValue`` payload that happens to use the same key
  (a categorical generation value such as ``{"label": "North"}``).

Dropping keys by name during a blind recursive walk conflates the two and lets
two semantically different plans hash identically. This module walks the
pydantic models instead, so a field is dropped only when the model that owns it
declares it presentation-only through :attr:`display_only_fields`. Anything
reached inside a mapping is opaque user content and is preserved verbatim.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel


class CanonicalContentError(TypeError):
    """A value cannot be represented in canonical execution content."""


def declared_display_only_fields(model: type[BaseModel]) -> frozenset[str]:
    """Return the presentation-only field names declared by ``model``.

    The declaration is a ``ClassVar[frozenset[str]]`` named
    ``display_only_fields``. Anything else (including a same-named model field)
    is ignored so that untrusted payloads can never influence the projection.
    """

    declared = model.__dict__.get("display_only_fields")
    if declared is None:
        declared = getattr(model, "display_only_fields", None)
    if declared is None or isinstance(declared, (BaseModel, property)):
        return frozenset()
    if not isinstance(declared, (frozenset, set, tuple, list)):
        return frozenset()
    if any(not isinstance(name, str) for name in declared):
        return frozenset()
    return frozenset(declared)


def undeclared_display_only_fields(model: type[BaseModel]) -> frozenset[str]:
    """Return declared names that are not real fields of ``model``.

    Used by the contract tests so a renamed field cannot silently reintroduce
    presentation text into the canonical hash.
    """

    return declared_display_only_fields(model).difference(model.model_fields)


def canonical_content(value: object) -> object:
    """Project ``value`` onto its JSON-safe execution semantics.

    Presentation-only fields are removed by field identity at every model
    boundary. Mappings are opaque data: their keys are preserved exactly.
    """

    if isinstance(value, BaseModel):
        model = type(value)
        skipped = declared_display_only_fields(model)
        return {
            name: canonical_content(getattr(value, name))
            for name in model.model_fields
            if name not in skipped
        }
    if isinstance(value, Mapping):
        return {
            _canonical_key(key): canonical_content(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [canonical_content(item) for item in value]
    if isinstance(value, Enum):
        return canonical_content(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalContentError(
                "canonical execution content cannot contain a non-finite number"
            )
        return value
    raise CanonicalContentError(
        f"canonical execution content cannot contain {type(value)!r}"
    )


def _canonical_key(key: object) -> str:
    if isinstance(key, Enum):
        return _canonical_key(key.value)
    if isinstance(key, str):
        return key
    raise CanonicalContentError(
        f"canonical execution content cannot use {type(key)!r} as a mapping key"
    )


__all__ = [
    "CanonicalContentError",
    "canonical_content",
    "declared_display_only_fields",
    "undeclared_display_only_fields",
]
