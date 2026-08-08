from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from collections.abc import Mapping

from ..privacy import PrivacyGateway


_ALLOWED_DIMENSIONS = frozenset(
    {
        "run_id",
        "workspace_id",
        "phase",
        "operation",
        "dataset_count",
        "row_count",
        "column_count",
        "plan_step_count",
        "duration_ms",
        "error_code",
        "retry_count",
        "lease_attempt",
        "outcome",
        "status",
    }
)


class PrivacySafeLogFilter(logging.Filter):
    """Redact messages and exception values emitted by analysis components."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = PrivacyGateway.redact_sensitive_text(message)
        record.args = ()

        if record.exc_info is not None:
            exception_type, _exception, traceback = record.exc_info
            try:
                safe_exception = exception_type("details redacted")
            except Exception:
                safe_exception = RuntimeError("details redacted")
            record.exc_info = (
                type(safe_exception),
                safe_exception,
                traceback,
            )
            record.exc_text = None
        return True


class AnalysisJsonFormatter(logging.Formatter):
    """Compact JSON formatter for local logs and container stdout."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        analysis = getattr(record, "analysis", None)
        if isinstance(analysis, Mapping):
            payload["analysis"] = dict(analysis)
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )


def configure_analysis_json_logging(
    *,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure one idempotent JSON stdout boundary for this pipeline."""

    namespace = logging.getLogger("scripts.data_analysis_agent")
    namespace.setLevel(level)
    if not any(
        getattr(handler, "_docmind_analysis_json", False)
        for handler in namespace.handlers
    ):
        handler = logging.StreamHandler()
        handler.setLevel(level)
        handler.setFormatter(AnalysisJsonFormatter())
        handler.addFilter(PrivacySafeLogFilter())
        handler._docmind_analysis_json = True  # type: ignore[attr-defined]
        namespace.addHandler(handler)
    namespace.propagate = False
    return namespace


def get_analysis_logger(name: str) -> logging.Logger:
    """Return an analysis logger with privacy filtering installed once."""

    logger = logging.getLogger(name)
    if not any(
        isinstance(item, PrivacySafeLogFilter) for item in logger.filters
    ):
        logger.addFilter(PrivacySafeLogFilter())
    return logger


def safe_analysis_dimensions(
    values: Mapping[str, object],
) -> dict[str, str | int | float | bool | None]:
    """Build a bounded allow-listed log payload that cannot contain rows."""

    unexpected = set(values).difference(_ALLOWED_DIMENSIONS)
    if unexpected:
        raise ValueError(
            "unsupported analysis log dimensions: "
            + ", ".join(sorted(unexpected))
        )
    output: dict[str, str | int | float | bool | None] = {}
    for key, value in values.items():
        if value is None or isinstance(value, (int, float, bool)):
            output[key] = value
            continue
        normalized = " ".join(str(value).split())[:200]
        output[key] = PrivacyGateway.redact_sensitive_text(normalized)
    return output


def log_analysis_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **dimensions: object,
) -> None:
    """Emit one structured control-plane log without prompt or cell data."""

    safe_event = " ".join(str(event).split())[:120]
    logger.log(
        level,
        "analysis_event",
        extra={
            "analysis": {
                "event": safe_event,
                **safe_analysis_dimensions(dimensions),
            }
        },
    )


__all__ = [
    "AnalysisJsonFormatter",
    "PrivacySafeLogFilter",
    "configure_analysis_json_logging",
    "get_analysis_logger",
    "log_analysis_event",
    "safe_analysis_dimensions",
]
