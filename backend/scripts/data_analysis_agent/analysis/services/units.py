from __future__ import annotations

import re
from dataclasses import dataclass


_CURRENCY_PATTERNS = (
    (re.compile(r"(?:\busd\b|\bus\s*dollars?\b|\bdollars?\b|\$)", re.I), "USD"),
    (re.compile(r"(?:\beur\b|\beuros?\b|€)", re.I), "EUR"),
    (re.compile(r"(?:\bgbp\b|\bpounds?\b|£)", re.I), "GBP"),
    (re.compile(r"(?:\binr\b|\brupees?\b|₹)", re.I), "INR"),
)
_SCALE_PATTERNS = (
    (re.compile(r"\b(?:billions?|bn)\b", re.I), "billion"),
    (re.compile(r"\b(?:millions?|mn)\b", re.I), "million"),
    (re.compile(r"\b(?:thousands?|000s?|k)\b", re.I), "thousand"),
    (re.compile(r"\bcrores?\b", re.I), "crore"),
    (re.compile(r"\blakhs?\b", re.I), "lakh"),
)
_PERCENT_RE = re.compile(r"(?:%|\bpercent(?:age)?\b)", re.I)
_AREA_RE = re.compile(
    r"\b(?:acres?|hectares?|square\s+(?:feet|foot|meters?|kilometers?|miles?))\b",
    re.I,
)
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class UnitSignature:
    family: str | None
    scale: str | None
    canonical: str | None


def unit_signature(value: object) -> UnitSignature:
    text = _SPACE_RE.sub(" ", str(value or "")).strip()
    if not text:
        return UnitSignature(None, None, None)
    if _PERCENT_RE.search(text):
        return UnitSignature("percent", None, "percent")
    family = next(
        (name for pattern, name in _CURRENCY_PATTERNS if pattern.search(text)),
        None,
    )
    scale = next(
        (name for pattern, name in _SCALE_PATTERNS if pattern.search(text)),
        None,
    )
    area_match = _AREA_RE.search(text)
    if area_match:
        area = area_match.group(0).casefold()
        if area.endswith("s"):
            area = area[:-1]
        return UnitSignature(
            "area",
            scale,
            " ".join(part for part in (scale, area) if part),
        )
    if family or scale:
        canonical = " ".join(part for part in (family, scale) if part)
        return UnitSignature(family or "scale", scale, canonical)
    return UnitSignature(text.casefold(), None, text)


def canonical_unit(value: object) -> str | None:
    return unit_signature(value).canonical


def units_compatible(required: object, available: object) -> bool:
    expected = unit_signature(required)
    actual = unit_signature(available)
    if expected.canonical is None or actual.canonical is None:
        return False
    if expected.canonical.casefold() == actual.canonical.casefold():
        return True
    if expected.family != actual.family:
        return False
    if expected.scale and actual.scale != expected.scale:
        return False
    return True


def merge_unit_scale(base: object, hint: object) -> str | None:
    primary = unit_signature(base)
    secondary = unit_signature(hint)
    if primary.canonical is None:
        return secondary.canonical
    if secondary.canonical is None or primary.family == "percent":
        return primary.canonical
    if (
        primary.family == secondary.family
        and primary.scale is None
        and secondary.scale is not None
    ):
        return secondary.canonical
    if (
        secondary.family == "scale"
        and primary.family not in {None, "scale"}
        and primary.scale is None
    ):
        return " ".join((primary.family, secondary.scale or "")).strip()
    if primary.family == "scale" and secondary.family not in {None, "scale"}:
        return " ".join((secondary.family, primary.scale or "")).strip()
    return primary.canonical


def table_unit_hint(*values: object) -> str | None:
    """Prefer explicit currency/scale context over incidental percentage labels."""

    text = " ".join(str(value or "") for value in values)
    currency = next(
        (name for pattern, name in _CURRENCY_PATTERNS if pattern.search(text)),
        None,
    )
    scale = next(
        (name for pattern, name in _SCALE_PATTERNS if pattern.search(text)),
        None,
    )
    if currency or scale:
        return " ".join(part for part in (currency, scale) if part)
    return "percent" if _PERCENT_RE.search(text) else None


def resolved_column_unit(
    *,
    declared: object,
    detected: object,
    table_hint: object,
    is_measure: bool,
) -> str | None:
    if not is_measure:
        return None
    local = canonical_unit(detected)
    declared_unit = canonical_unit(declared)
    base = local or declared_unit
    return merge_unit_scale(base, table_hint)


def resolved_row_unit(
    *,
    column_unit: object,
    table_hint: object,
    row_text: object,
) -> str | None:
    if _PERCENT_RE.search(str(row_text or "")):
        return "percent"
    return merge_unit_scale(column_unit, table_hint)
