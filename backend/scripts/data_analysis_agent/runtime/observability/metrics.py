from __future__ import annotations

from collections import Counter, defaultdict
from threading import Lock

from ..models.runs import StageTokenUsage


class AnalysisMetrics:
    """Small process-local metrics registry for a self-hosted portfolio runtime."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._run_outcomes: Counter[str] = Counter()
        self._errors: Counter[str] = Counter()
        self._phase_count: Counter[str] = Counter()
        self._phase_duration_ms: defaultdict[str, float] = defaultdict(float)
        self._llm_calls: Counter[str] = Counter()
        self._llm_input_tokens: Counter[str] = Counter()
        self._llm_output_tokens: Counter[str] = Counter()
        self._llm_cost_usd: defaultdict[str, float] = defaultdict(float)

    def record_run_outcome(self, outcome: str) -> None:
        with self._lock:
            self._run_outcomes[outcome] += 1

    def record_error(self, code: str) -> None:
        with self._lock:
            self._errors[code] += 1

    def observe_phase(self, phase: str, duration_ms: float) -> None:
        with self._lock:
            self._phase_count[phase] += 1
            self._phase_duration_ms[phase] += max(0.0, duration_ms)

    def observe_llm(self, item: StageTokenUsage) -> None:
        with self._lock:
            self._llm_calls[item.stage] += item.call_count
            self._llm_input_tokens[item.stage] += item.usage.input_tokens
            self._llm_output_tokens[item.stage] += item.usage.output_tokens
            self._llm_cost_usd[item.stage] += item.usage.estimated_cost_usd

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            phases = {
                phase: {
                    "count": count,
                    "total_duration_ms": self._phase_duration_ms[phase],
                    "average_duration_ms": (
                        self._phase_duration_ms[phase] / count if count else 0
                    ),
                }
                for phase, count in sorted(self._phase_count.items())
            }
            llm = {
                stage: {
                    "call_count": count,
                    "input_tokens": self._llm_input_tokens[stage],
                    "output_tokens": self._llm_output_tokens[stage],
                    "estimated_cost_usd": self._llm_cost_usd[stage],
                }
                for stage, count in sorted(self._llm_calls.items())
            }
            return {
                "run_outcomes": dict(sorted(self._run_outcomes.items())),
                "errors": dict(sorted(self._errors.items())),
                "phases": phases,
                "llm": llm,
            }

    def reset(self) -> None:
        with self._lock:
            self._run_outcomes.clear()
            self._errors.clear()
            self._phase_count.clear()
            self._phase_duration_ms.clear()
            self._llm_calls.clear()
            self._llm_input_tokens.clear()
            self._llm_output_tokens.clear()
            self._llm_cost_usd.clear()


analysis_metrics = AnalysisMetrics()


__all__ = ["AnalysisMetrics", "analysis_metrics"]
