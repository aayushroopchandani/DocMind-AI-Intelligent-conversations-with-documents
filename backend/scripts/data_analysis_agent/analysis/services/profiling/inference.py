from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ...models import ProfiledDataType


_MISSING_TEXT = {
    "",
    "-",
    "--",
    "—",
    "–",
    "n/a",
    "na",
    "n.m.",
    "nm",
    "nil",
    "none",
    "null",
    "not available",
}
_BOOLEAN_TRUE = {"true", "yes", "y"}
_BOOLEAN_FALSE = {"false", "no", "n"}
_CALENDAR_YEAR_RE = re.compile(r"^(?P<year>(?:19|20)\d{2})$")
_FISCAL_YEAR_RE = re.compile(
    r"^(?:fy|fiscal\s*year)\s*['’]?(?P<year>(?:19|20)?\d{2})$",
    re.IGNORECASE,
)
_YEAR_RANGE_RE = re.compile(
    r"^(?P<start>(?:19|20)\d{2})\s*[-–/]\s*(?P<end>\d{2}|(?:19|20)\d{2})$"
)
_QUARTER_RE = re.compile(
    r"^(?:q(?P<q1>[1-4])\s*[-/]?\s*(?P<y1>(?:19|20)\d{2})|"
    r"(?P<y2>(?:19|20)\d{2})\s*[-/]?\s*q(?P<q2>[1-4]))$",
    re.IGNORECASE,
)
_MONTH_RE = re.compile(
    r"^(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)(?:\s+|[-/])?(?P<year>(?:19|20)\d{2})?$",
    re.IGNORECASE,
)
_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_CURRENCY_OR_SCALE_RE = re.compile(
    r"(?:₹|\$|€|£|\b(?:usd|inr|eur|gbp|crores?|lakhs?|millions?|"
    r"billions?|thousands?|mn|bn|k)\b)",
    re.IGNORECASE,
)
_TIME_LABEL_RE = re.compile(
    r"\b(?:date|year|fiscal|period|quarter|month|fy)\b", re.IGNORECASE
)
_UNIT_CURRENCY_PATTERNS = (
    (re.compile(r"₹|\binr\b", re.IGNORECASE), "INR"),
    (re.compile(r"\$|\busd\b", re.IGNORECASE), "USD"),
    (re.compile(r"€|\beur\b", re.IGNORECASE), "EUR"),
    (re.compile(r"£|\bgbp\b", re.IGNORECASE), "GBP"),
)
_UNIT_SCALE_PATTERNS = (
    (re.compile(r"\b(?:billion|billions|bn)\b", re.IGNORECASE), "billion"),
    (re.compile(r"\b(?:million|millions|mn)\b", re.IGNORECASE), "million"),
    (re.compile(r"\b(?:thousand|thousands|k)\b", re.IGNORECASE), "thousand"),
    (re.compile(r"\b(?:crore|crores)\b", re.IGNORECASE), "crore"),
    (re.compile(r"\b(?:lakh|lakhs)\b", re.IGNORECASE), "lakh"),
)
_MONTH_NUMBERS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


@dataclass(frozen=True, slots=True)
class PeriodValue:
    format: str
    label: str
    sort_key: tuple[int, int, int]
    interval_index: int | None = None


@dataclass(frozen=True, slots=True)
class ValueObservation:
    kind: ProfiledDataType
    display: str
    canonical: str
    numeric: float | None = None
    period: PeriodValue | None = None


def normalize_text(value: Any) -> str:
    normalized = "" if value is None else str(value)
    return " ".join(normalized.split()).strip()


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and normalize_text(value).casefold() in _MISSING_TEXT


def canonical_value(value: Any) -> str:
    if isinstance(value, str):
        return normalize_text(value).casefold()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    return str(value)


def _four_digit_year(value: str) -> int:
    year = int(value)
    return 2000 + year if year < 100 else year


def parse_period(value: Any, *, label: str = "") -> PeriodValue | None:
    if isinstance(value, datetime):
        normalized = value.date().isoformat()
        return PeriodValue("date", normalized, (value.year, value.month, value.day))
    if isinstance(value, date):
        return PeriodValue(
            "date",
            value.isoformat(),
            (value.year, value.month, value.day),
        )

    text = normalize_text(value)
    if not text:
        return None
    match = _FISCAL_YEAR_RE.fullmatch(text)
    if match:
        year = _four_digit_year(match.group("year"))
        return PeriodValue("fiscal_year", f"FY{year}", (year, 0, 0), year)
    match = _YEAR_RANGE_RE.fullmatch(text)
    if match:
        start = int(match.group("start"))
        end = _four_digit_year(match.group("end"))
        if end < start:
            end += 100
        return PeriodValue(
            "year_range",
            f"{start}-{str(end)[-2:]}",
            (start, end, 0),
            start,
        )
    match = _QUARTER_RE.fullmatch(text)
    if match:
        quarter = int(match.group("q1") or match.group("q2"))
        year = int(match.group("y1") or match.group("y2"))
        return PeriodValue(
            "quarter",
            f"{year} Q{quarter}",
            (year, quarter, 0),
            (year * 4) + quarter - 1,
        )
    match = _MONTH_RE.fullmatch(text)
    if match:
        month = _MONTH_NUMBERS[match.group("month")[:3].casefold()]
        year_text = match.group("year")
        year = int(year_text) if year_text else 0
        return PeriodValue(
            "month",
            f"{year:04d}-{month:02d}" if year else f"month-{month:02d}",
            (year, month, 0),
            ((year * 12) + month - 1) if year else None,
        )
    match = _CALENDAR_YEAR_RE.fullmatch(text)
    if match and (
        _TIME_LABEL_RE.search(label)
        or normalize_text(label).casefold() == text.casefold()
        or not normalize_text(label)
    ):
        year = int(match.group("year"))
        return PeriodValue("calendar_year", str(year), (year, 0, 0), year)

    iso_candidate = text.replace("/", "-")
    try:
        parsed = date.fromisoformat(iso_candidate)
    except ValueError:
        parsed = None
    if parsed is not None:
        return PeriodValue(
            "date",
            parsed.isoformat(),
            (parsed.year, parsed.month, parsed.day),
        )
    for pattern in ("%d-%m-%Y", "%m-%d-%Y", "%d-%b-%Y", "%b-%d-%Y"):
        try:
            parsed_date = datetime.strptime(text, pattern).date()
        except ValueError:
            continue
        return PeriodValue(
            "date",
            parsed_date.isoformat(),
            (parsed_date.year, parsed_date.month, parsed_date.day),
        )
    return None


def _numeric_value(value: Any) -> tuple[float, bool] | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        return numeric, numeric.is_integer()
    if not isinstance(value, str):
        return None

    text = normalize_text(value)
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    text = _CURRENCY_OR_SCALE_RE.sub("", text)
    text = text.replace(",", "").replace("%", "").replace(" ", "")
    if not _NUMERIC_RE.fullmatch(text):
        return None
    numeric = float(text)
    if negative:
        numeric = -numeric
    return numeric, numeric.is_integer()


def observe_value(value: Any, *, label: str) -> ValueObservation | None:
    if is_missing(value):
        return None
    display = normalize_text(value) if isinstance(value, str) else str(value)
    canonical = canonical_value(value)

    if isinstance(value, bool):
        return ValueObservation(ProfiledDataType.BOOLEAN, display, canonical)
    if isinstance(value, str):
        lowered = display.casefold()
        if lowered in _BOOLEAN_TRUE | _BOOLEAN_FALSE:
            return ValueObservation(ProfiledDataType.BOOLEAN, display, canonical)

    period = parse_period(value, label=label)
    if period is not None:
        kind = {
            "calendar_year": ProfiledDataType.CALENDAR_YEAR,
            "fiscal_year": ProfiledDataType.FISCAL_PERIOD,
            "year_range": ProfiledDataType.FISCAL_PERIOD,
            "quarter": ProfiledDataType.QUARTER,
            "month": ProfiledDataType.MONTH,
            "date": ProfiledDataType.DATE,
        }[period.format]
        return ValueObservation(kind, display, canonical, period=period)

    numeric_result = _numeric_value(value)
    if numeric_result is not None:
        numeric, is_integer = numeric_result
        return ValueObservation(
            ProfiledDataType.INTEGER if is_integer else ProfiledDataType.NUMBER,
            display,
            canonical,
            numeric=numeric,
        )
    return ValueObservation(ProfiledDataType.STRING, display, canonical)


def detect_unit(*values: Any) -> str | None:
    text = " ".join(normalize_text(value) for value in values if value is not None)
    if not text:
        return None
    if "%" in text or re.search(r"\bpercent(?:age)?\b", text, re.IGNORECASE):
        return "percent"
    currency = next(
        (name for pattern, name in _UNIT_CURRENCY_PATTERNS if pattern.search(text)),
        None,
    )
    scale = next(
        (name for pattern, name in _UNIT_SCALE_PATTERNS if pattern.search(text)),
        None,
    )
    return " ".join(part for part in (currency, scale) if part) or None
