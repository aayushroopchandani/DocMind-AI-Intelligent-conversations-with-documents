from __future__ import annotations

import re
import unicodedata


_OXFORD_CONJUNCTION_RE = re.compile(r",\s+(?:and)\s+", re.IGNORECASE)
_NON_TOKEN_RE = re.compile(r"[^\w%$€£₹]+", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def canonical_query_cache_text(value: object) -> str:
    """Normalize presentation-only query differences without changing intent."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = _OXFORD_CONJUNCTION_RE.sub(", ", text)
    text = text.replace("&", " and ")
    text = text.replace("%", " percent ")
    text = _NON_TOKEN_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()
