"""Naming a sheet the workbook will actually accept (Phase 9.11.3).

Spreadsheet sheet names are a small minefield: five forbidden characters, a
31-character limit, no leading or trailing apostrophe, a reserved word, and
case-insensitive uniqueness. A name that violates any of these is rejected by
the client *after* the user has approved the patch, which is the worst possible
moment to discover it.

So names are sanitized and de-duplicated here, deterministically: the same
result compiled twice against the same workbook produces the same sheet name and
the same sheet ID. That determinism is what lets a patch be recompiled after a
rebase without the target moving.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable


MAX_SHEET_NAME_LENGTH = 31
DEFAULT_SHEET_NAME = "AI Result"

_FORBIDDEN_CHARACTERS = re.compile(r"[\[\]:*?/\\]")
_WHITESPACE = re.compile(r"\s+")
_RESERVED_NAMES = frozenset({"history"})


def sanitize_sheet_name(
    value: str,
    *,
    fallback: str = DEFAULT_SHEET_NAME,
) -> str:
    """Return `value` as a name a spreadsheet will accept."""

    candidate = _FORBIDDEN_CHARACTERS.sub(" ", value or "")
    candidate = _WHITESPACE.sub(" ", candidate).strip().strip("'").strip()
    candidate = candidate[:MAX_SHEET_NAME_LENGTH].strip()
    if not candidate or candidate.casefold() in _RESERVED_NAMES:
        return fallback[:MAX_SHEET_NAME_LENGTH]
    return candidate


def unique_sheet_name(
    base: str,
    existing: Iterable[str],
    *,
    fallback: str = DEFAULT_SHEET_NAME,
) -> str:
    """Return a sanitized name that collides with nothing in `existing`.

    Collisions resolve to `Base (2)`, `Base (3)` and so on, with the base
    trimmed just enough to keep the whole name inside the 31-character limit —
    the suffix is what has to survive, not the last word of the label.
    """

    taken = {str(name).strip().casefold() for name in existing}
    candidate = sanitize_sheet_name(base, fallback=fallback)
    if candidate.casefold() not in taken:
        return candidate
    for ordinal in range(2, 1_000):
        suffix = f" ({ordinal})"
        trimmed = candidate[: MAX_SHEET_NAME_LENGTH - len(suffix)].strip()
        attempt = f"{trimmed}{suffix}"
        if attempt.casefold() not in taken:
            return attempt
    # A workbook with a thousand same-named sheets is not a naming problem.
    raise ValueError(f"no free sheet name derived from '{base}'")


def deterministic_worksheet_id(*parts: str) -> str:
    """Return a stable synthetic worksheet ID for a sheet not yet created.

    Later operations in the patch bind to this ID rather than to the display
    name (9.11.3), so a rename between approval and application cannot make the
    write land on the wrong sheet. It is derived, not random, so recompiling the
    same patch addresses the same sheet.
    """

    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"sheet-{digest[:24]}"


__all__ = [
    "DEFAULT_SHEET_NAME",
    "MAX_SHEET_NAME_LENGTH",
    "deterministic_worksheet_id",
    "sanitize_sheet_name",
    "unique_sheet_name",
]
