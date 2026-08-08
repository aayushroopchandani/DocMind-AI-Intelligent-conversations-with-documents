from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Sequence

from ..models.privacy import AnalysisPrivacyMode, DataSensitivity


_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{3}\)?[ .-]?)"
    r"\d{3}[ .-]?\d{4}(?!\d)"
)
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|"
    r"client[_ -]?secret|private[_ -]?key)\b\s*[:=]\s*[^\s,;]+"
    r"|\b(?:sk|pk|rk)-[A-Za-z0-9_-]{12,}"
    r"|\bBearer\s+[A-Za-z0-9._~+/-]{12,}={0,2}"
    r"|(?:mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis)://[^\s]+"
    r")"
)
_SIGNED_URL_RE = re.compile(
    r"(?i)https?://[^\s]+(?:"
    r"[?&](?:signature|token|api[_-]?key|x-amz-signature|__cld_token__)="
    r"|/s--[A-Za-z0-9_-]+--/"
    r")[^\s]*"
)
_HEADER_TOKEN_RE = re.compile(r"[^a-z0-9]+")

_EMAIL_HEADERS = frozenset({"email", "email address", "e mail"})
_PHONE_HEADERS = frozenset(
    {"phone", "phone number", "mobile", "mobile number", "telephone", "contact number"}
)
_CREDENTIAL_HEADER_TOKENS = frozenset(
    {"password", "passwd", "secret", "token", "credential", "api key", "connection string"}
)
_IDENTIFIER_HEADERS = frozenset(
    {
        "id",
        "identifier",
        "account number",
        "customer id",
        "employee id",
        "user id",
        "username",
        "ssn",
        "social security number",
        "aadhaar",
        "aadhar",
        "pan number",
        "passport number",
        "tax id",
    }
)

_CURRENT_PRIVACY_MODE: ContextVar[AnalysisPrivacyMode] = ContextVar(
    "analysis_privacy_mode",
    default=AnalysisPrivacyMode.STANDARD,
)


@dataclass(frozen=True, slots=True)
class PrivacyColumnResult:
    examples: tuple[str, ...]
    classification: DataSensitivity
    inspected_count: int
    redacted_count: int


def current_privacy_mode() -> AnalysisPrivacyMode:
    return _CURRENT_PRIVACY_MODE.get()


@contextmanager
def privacy_scope(mode: AnalysisPrivacyMode) -> Iterator[None]:
    token = _CURRENT_PRIVACY_MODE.set(mode)
    try:
        yield
    finally:
        _CURRENT_PRIVACY_MODE.reset(token)


class PrivacyGateway:
    """The only path from representative dataset values to LLM metadata.

    It intentionally uses deterministic, conservative rules. Classification
    metadata may be sent to a model; original sensitive values never are.
    """

    def sanitize_examples(
        self,
        *,
        column_key: str,
        label: str,
        semantic_role: str,
        values: Sequence[object],
        mode: AnalysisPrivacyMode | None = None,
    ) -> PrivacyColumnResult:
        selected_mode = mode or current_privacy_mode()
        normalized = tuple(
            " ".join(str(value).split())[:240]
            for value in values
            if str(value).strip()
        )
        classification = self.classify_column(
            column_key=column_key,
            label=label,
            semantic_role=semantic_role,
            values=normalized,
        )
        if selected_mode in {
            AnalysisPrivacyMode.SCHEMA_ONLY,
            AnalysisPrivacyMode.LOCAL_ONLY,
        }:
            return PrivacyColumnResult(
                examples=(),
                classification=classification,
                inspected_count=len(normalized),
                redacted_count=len(normalized),
            )
        if classification != DataSensitivity.NONE:
            return PrivacyColumnResult(
                examples=(),
                classification=classification,
                inspected_count=len(normalized),
                redacted_count=len(normalized),
            )

        safe: list[str] = []
        redacted = 0
        for value in normalized:
            scrubbed = self.redact_sensitive_text(value)
            if scrubbed != value:
                redacted += 1
            if scrubbed and scrubbed not in safe:
                safe.append(scrubbed)
        return PrivacyColumnResult(
            examples=tuple(safe[:3]),
            classification=classification,
            inspected_count=len(normalized),
            redacted_count=redacted,
        )

    def classify_column(
        self,
        *,
        column_key: str,
        label: str,
        semantic_role: str,
        values: Sequence[str],
    ) -> DataSensitivity:
        header = self._normalize_header(f"{column_key} {label}")
        label_only = self._normalize_header(label)
        if self._contains_header(header, _CREDENTIAL_HEADER_TOKENS):
            return DataSensitivity.CREDENTIAL
        if label_only in _EMAIL_HEADERS or "email" in header.split():
            return DataSensitivity.EMAIL
        if label_only in _PHONE_HEADERS or any(
            token in header.split() for token in ("phone", "mobile", "telephone")
        ):
            return DataSensitivity.PHONE
        if (
            label_only in _IDENTIFIER_HEADERS
            or header.endswith(" id")
            or semantic_role.casefold() in {"identifier", "id"}
        ):
            return DataSensitivity.IDENTIFIER
        for value in values:
            if _CREDENTIAL_RE.search(value):
                return DataSensitivity.CREDENTIAL
            if _EMAIL_RE.search(value):
                return DataSensitivity.EMAIL
            if _PHONE_RE.search(value):
                return DataSensitivity.PHONE
        return DataSensitivity.NONE

    @staticmethod
    def redact_sensitive_text(value: str) -> str:
        output = _SIGNED_URL_RE.sub("[REDACTED:SIGNED_URL]", value)
        output = _CREDENTIAL_RE.sub("[REDACTED:CREDENTIAL]", output)
        output = _EMAIL_RE.sub("[REDACTED:EMAIL]", output)
        output = _PHONE_RE.sub("[REDACTED:PHONE]", output)
        return output[:1_000]

    @staticmethod
    def contains_credential(value: str) -> bool:
        return bool(_CREDENTIAL_RE.search(value))

    @staticmethod
    def _normalize_header(value: str) -> str:
        return " ".join(_HEADER_TOKEN_RE.sub(" ", value.casefold()).split())

    @staticmethod
    def _contains_header(header: str, candidates: frozenset[str]) -> bool:
        return any(candidate in header for candidate in candidates)


__all__ = [
    "PrivacyColumnResult",
    "PrivacyGateway",
    "current_privacy_mode",
    "privacy_scope",
]
