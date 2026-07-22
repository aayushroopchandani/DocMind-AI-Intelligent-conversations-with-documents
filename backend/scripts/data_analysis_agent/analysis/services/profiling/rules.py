from __future__ import annotations

import re

from ...models import ProfiledDataType


ID_LABEL_RE = re.compile(
    r"(?:^|[_\s-])(?:id|identifier|code|account|reference|serial|sku|isin)"
    r"(?:$|[_\s-])",
    re.IGNORECASE,
)
METRIC_LABEL_RE = re.compile(
    r"\b(?:amount|balance|cost|count|expense|income|margin|profit|rate|ratio|"
    r"revenue|sales|total|value|volume|assets?|liabilit(?:y|ies)|cash|debt|"
    r"emissions?|percentage|percent)\b",
    re.IGNORECASE,
)
TOTAL_RE = re.compile(
    r"^(?:grand\s+)?(?:sub\s*)?total\b|\bnet\s+total\b", re.IGNORECASE
)
FOOTNOTE_RE = re.compile(
    r"^(?:\*+|†+|‡+|note\b|notes\b|source\s*:|excluding\b|including\b)",
    re.IGNORECASE,
)
TEMPORAL_TYPES = frozenset(
    {
        ProfiledDataType.CALENDAR_YEAR,
        ProfiledDataType.FISCAL_PERIOD,
        ProfiledDataType.QUARTER,
        ProfiledDataType.MONTH,
        ProfiledDataType.DATE,
    }
)
NUMERIC_TYPES = frozenset(
    {ProfiledDataType.INTEGER, ProfiledDataType.NUMBER}
)
