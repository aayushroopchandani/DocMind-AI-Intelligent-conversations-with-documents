from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

from ...models import (
    NumericColumnStatistics,
    ProfiledDataType,
    StringColumnStatistics,
    TimeColumnStatistics,
    ValueFrequency,
)
from .inference import PeriodValue, ValueObservation, normalize_text
from .rules import FOOTNOTE_RE, ID_LABEL_RE, NUMERIC_TYPES, TEMPORAL_TYPES, TOTAL_RE


def rounded(value: float) -> float:
    return round(float(value), 6)


def percentage(count: int, total: int) -> float:
    return rounded((count / total) * 100) if total else 0.0


def _percentile(sorted_values: Sequence[float], percentile: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] + (
        (sorted_values[upper] - sorted_values[lower]) * weight
    )


def numeric_statistics(values: list[float]) -> NumericColumnStatistics | None:
    if not values:
        return None
    ordered = sorted(values)
    mean = math.fsum(ordered) / len(ordered)
    variance = math.fsum((value - mean) ** 2 for value in ordered) / len(ordered)
    p05 = _percentile(ordered, 0.05)
    p25 = _percentile(ordered, 0.25)
    p50 = _percentile(ordered, 0.50)
    p75 = _percentile(ordered, 0.75)
    p95 = _percentile(ordered, 0.95)
    iqr = p75 - p25
    lower_bound = p25 - (1.5 * iqr)
    upper_bound = p75 + (1.5 * iqr)
    return NumericColumnStatistics(
        minimum=rounded(ordered[0]),
        maximum=rounded(ordered[-1]),
        mean=rounded(mean),
        median=rounded(p50),
        standard_deviation=rounded(math.sqrt(variance)),
        percentile_05=rounded(p05),
        percentile_25=rounded(p25),
        percentile_75=rounded(p75),
        percentile_95=rounded(p95),
        zero_count=sum(value == 0 for value in ordered),
        negative_count=sum(value < 0 for value in ordered),
        potential_outlier_count=sum(
            value < lower_bound or value > upper_bound for value in ordered
        ),
    )


def _missing_interval_labels(periods: Sequence[PeriodValue]) -> tuple[str, ...]:
    if not periods or any(period.interval_index is None for period in periods):
        return ()
    formats = {period.format for period in periods}
    if len(formats) != 1:
        return ()
    format_name = next(iter(formats))
    supported = {"calendar_year", "fiscal_year", "year_range", "quarter", "month"}
    if format_name not in supported:
        return ()
    observed = {int(period.interval_index) for period in periods}
    if not observed:
        return ()
    start, end = min(observed), max(observed)
    if end - start > 120:
        return ()

    def label(index: int) -> str:
        if format_name == "calendar_year":
            return str(index)
        if format_name == "fiscal_year":
            return f"FY{index}"
        if format_name == "year_range":
            return f"{index}-{str(index + 1)[-2:]}"
        if format_name == "quarter":
            return f"{index // 4} Q{(index % 4) + 1}"
        return f"{index // 12:04d}-{(index % 12) + 1:02d}"

    return tuple(
        label(index) for index in range(start, end + 1) if index not in observed
    )


def time_statistics(
    observations: Sequence[ValueObservation],
) -> TimeColumnStatistics | None:
    periods = [item.period for item in observations if item.period is not None]
    if not periods:
        return None
    ordered = sorted(periods, key=lambda period: period.sort_key)
    return TimeColumnStatistics(
        detected_formats=tuple(dict.fromkeys(period.format for period in periods)),
        minimum_period=ordered[0].label,
        maximum_period=ordered[-1].label,
        missing_intervals=_missing_interval_labels(periods),
    )


def infer_type(
    observations: Sequence[ValueObservation],
) -> tuple[ProfiledDataType, float]:
    if not observations:
        return ProfiledDataType.EMPTY, 1.0
    counts = Counter(observation.kind for observation in observations)
    total = len(observations)
    numeric_count = sum(counts[kind] for kind in NUMERIC_TYPES)
    temporal_count = sum(counts[kind] for kind in TEMPORAL_TYPES)
    if numeric_count / total >= 0.90:
        inferred = (
            ProfiledDataType.INTEGER
            if counts[ProfiledDataType.NUMBER] == 0
            else ProfiledDataType.NUMBER
        )
        return inferred, numeric_count / total
    if temporal_count / total >= 0.90:
        temporal_counts = {kind: counts[kind] for kind in TEMPORAL_TYPES}
        inferred = max(
            temporal_counts,
            key=lambda kind: (temporal_counts[kind], kind.value),
        )
        return inferred, temporal_count / total
    inferred, count = counts.most_common(1)[0]
    confidence = count / total
    if confidence >= 0.90:
        return inferred, confidence
    return ProfiledDataType.MIXED, confidence


def declared_type_matches(declared: str, inferred: ProfiledDataType) -> bool:
    if inferred == ProfiledDataType.EMPTY:
        return True
    return {
        "number": inferred in NUMERIC_TYPES,
        "boolean": inferred == ProfiledDataType.BOOLEAN,
        "date": inferred in TEMPORAL_TYPES,
        "string": inferred
        in {
            ProfiledDataType.STRING,
            ProfiledDataType.CALENDAR_YEAR,
            ProfiledDataType.FISCAL_PERIOD,
            ProfiledDataType.QUARTER,
            ProfiledDataType.MONTH,
            ProfiledDataType.DATE,
        },
    }.get(declared, False)


def string_statistics(
    *,
    observations: Sequence[ValueObservation],
    label: str,
    unique_count: int,
) -> StringColumnStatistics | None:
    strings = [item for item in observations if item.kind == ProfiledDataType.STRING]
    if not strings:
        return None
    frequencies = Counter(item.canonical for item in strings)
    displays: dict[str, str] = {}
    for item in strings:
        displays.setdefault(item.canonical, item.display)
    ordered_frequencies = sorted(
        frequencies.items(),
        key=lambda item: (-item[1], item[0]),
    )[:5]
    non_null_count = len(observations)
    cardinality_ratio = unique_count / non_null_count if non_null_count else 0.0
    average_length = math.fsum(len(item.display) for item in strings) / len(strings)
    possible_identifier = bool(ID_LABEL_RE.search(label)) or (
        non_null_count >= 10
        and cardinality_ratio >= 0.90
        and average_length <= 80
    )
    low_cardinality = unique_count <= 20 and (
        non_null_count <= 10 or cardinality_ratio <= 0.50
    )
    normalized_label = normalize_text(label).casefold()
    return StringColumnStatistics(
        most_frequent_values=tuple(
            ValueFrequency(
                value=displays[value],
                count=count,
                percentage=percentage(count, len(strings)),
            )
            for value, count in ordered_frequencies
        ),
        average_text_length=rounded(average_length),
        possible_identifier=possible_identifier,
        low_cardinality=low_cardinality,
        repeated_header_value_count=sum(
            item.canonical == normalized_label for item in strings
        ),
        total_or_subtotal_value_count=sum(
            bool(TOTAL_RE.search(item.display)) for item in strings
        ),
        footnote_like_value_count=sum(
            bool(FOOTNOTE_RE.search(item.display)) for item in strings
        ),
    )
