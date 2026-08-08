from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from time import monotonic

from langchain_core.callbacks import get_usage_metadata_callback

from ..models.runs import StageTokenUsage, TokenUsage
from .metrics import analysis_metrics


@dataclass(frozen=True, slots=True)
class _ModelRate:
    input_usd_per_million: float
    output_usd_per_million: float


@dataclass(slots=True)
class LlmCallMeasurement:
    stage: str
    model: str
    prompt_version: str
    result: StageTokenUsage | None = None


@dataclass(slots=True)
class LlmUsageLedger:
    _records: dict[str, StageTokenUsage] = field(default_factory=dict)

    def record(self, item: StageTokenUsage) -> None:
        previous = self._records.get(item.stage)
        self._records[item.stage] = (
            item if previous is None else merge_stage_usage(previous, item)
        )

    def snapshot(self) -> dict[str, StageTokenUsage]:
        return dict(sorted(self._records.items()))


_ACTIVE_LEDGER: ContextVar[LlmUsageLedger | None] = ContextVar(
    "analysis_llm_usage_ledger",
    default=None,
)


@lru_cache(maxsize=1)
def _pricing() -> tuple[str, dict[str, _ModelRate]]:
    version = (
        os.getenv("ANALYSIS_MODEL_PRICING_VERSION", "unconfigured").strip()
        or "unconfigured"
    )
    raw = os.getenv("ANALYSIS_MODEL_PRICING_JSON", "").strip()
    if not raw:
        return version, {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return "invalid", {}
    if not isinstance(payload, Mapping):
        return "invalid", {}
    rates: dict[str, _ModelRate] = {}
    for model, value in payload.items():
        if not isinstance(model, str) or not isinstance(value, Mapping):
            continue
        try:
            input_rate = float(value.get("input_usd_per_million"))
            output_rate = float(value.get("output_usd_per_million"))
        except (TypeError, ValueError):
            continue
        if input_rate < 0 or output_rate < 0:
            continue
        rates[model] = _ModelRate(input_rate, output_rate)
    return version, rates


@contextmanager
def capture_llm_usage() -> Iterator[LlmUsageLedger]:
    ledger = LlmUsageLedger()
    token = _ACTIVE_LEDGER.set(ledger)
    try:
        yield ledger
    finally:
        _ACTIVE_LEDGER.reset(token)


@contextmanager
def measure_llm_call(
    *,
    stage: str,
    model: str,
    prompt_version: str,
) -> Iterator[LlmCallMeasurement]:
    """Measure one model call without retaining its prompts or output."""

    measurement = LlmCallMeasurement(
        stage=stage,
        model=model,
        prompt_version=prompt_version,
    )
    started = monotonic()
    with get_usage_metadata_callback() as callback:
        try:
            yield measurement
        finally:
            input_tokens, output_tokens = _metadata_tokens(
                callback.usage_metadata
            )
            pricing_version, rates = _pricing()
            rate = rates.get(model)
            cost = 0.0
            if rate is not None:
                cost = (
                    input_tokens * rate.input_usd_per_million
                    + output_tokens * rate.output_usd_per_million
                ) / 1_000_000
            usage = StageTokenUsage(
                stage=stage,
                model=model,
                prompt_version=prompt_version,
                pricing_version=pricing_version,
                pricing_configured=rate is not None,
                call_count=1,
                duration_ms=(monotonic() - started) * 1000,
                usage=TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    estimated_cost_usd=max(0.0, cost),
                ),
            )
            measurement.result = usage
            ledger = _ACTIVE_LEDGER.get()
            if ledger is not None:
                ledger.record(usage)
            analysis_metrics.observe_llm(usage)


def merge_stage_usage(
    left: StageTokenUsage,
    right: StageTokenUsage,
) -> StageTokenUsage:
    if (
        left.stage != right.stage
        or left.model != right.model
        or left.prompt_version != right.prompt_version
    ):
        raise ValueError("only identical LLM stages can be merged")
    usage = TokenUsage(
        input_tokens=left.usage.input_tokens + right.usage.input_tokens,
        output_tokens=left.usage.output_tokens + right.usage.output_tokens,
        total_tokens=left.usage.total_tokens + right.usage.total_tokens,
        estimated_cost_usd=(
            left.usage.estimated_cost_usd + right.usage.estimated_cost_usd
        ),
    )
    return StageTokenUsage(
        stage=left.stage,
        model=left.model,
        prompt_version=left.prompt_version,
        pricing_version=(
            left.pricing_version
            if left.pricing_version == right.pricing_version
            else "mixed"
        ),
        pricing_configured=(
            left.pricing_configured and right.pricing_configured
        ),
        call_count=left.call_count + right.call_count,
        duration_ms=left.duration_ms + right.duration_ms,
        usage=usage,
    )


def merge_stage_maps(
    *values: Mapping[str, StageTokenUsage],
) -> dict[str, StageTokenUsage]:
    ledger = LlmUsageLedger()
    for value in values:
        for item in value.values():
            ledger.record(item)
    return ledger.snapshot()


def total_token_usage(values: Mapping[str, StageTokenUsage]) -> TokenUsage:
    input_tokens = sum(item.usage.input_tokens for item in values.values())
    output_tokens = sum(item.usage.output_tokens for item in values.values())
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimated_cost_usd=sum(
            item.usage.estimated_cost_usd for item in values.values()
        ),
    )


def _metadata_tokens(values: Mapping[str, object]) -> tuple[int, int]:
    input_tokens = 0
    output_tokens = 0
    for value in values.values():
        if not isinstance(value, Mapping):
            continue
        try:
            input_tokens += max(0, int(value.get("input_tokens") or 0))
            output_tokens += max(0, int(value.get("output_tokens") or 0))
        except (TypeError, ValueError):
            continue
    return input_tokens, output_tokens


__all__ = [
    "LlmCallMeasurement",
    "LlmUsageLedger",
    "capture_llm_usage",
    "measure_llm_call",
    "merge_stage_maps",
    "merge_stage_usage",
    "total_token_usage",
]
