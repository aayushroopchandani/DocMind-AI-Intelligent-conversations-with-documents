from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


ASGIMessage = dict[str, Any]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]


class _RequestBodyTooLarge(Exception):
    pass


@dataclass(frozen=True, slots=True)
class RequestBodyLimit:
    max_body_bytes: int
    error_code: str
    message: str

    def __post_init__(self) -> None:
        if self.max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        if not self.error_code.strip() or not self.message.strip():
            raise ValueError("body-limit errors must be non-empty")


class AnalysisRequestBodyLimitMiddleware:
    """Enforce route-specific limits before JSON or multipart parsing.

    ``Content-Length`` is only a fast path. The wrapped ASGI receiver also
    counts actual bytes, so chunked requests and forged or missing length
    headers cannot bypass a policy.
    """

    def __init__(
        self,
        app: Any,
        *,
        limits: dict[str, RequestBodyLimit],
    ) -> None:
        if not limits:
            raise ValueError("at least one body limit is required")
        for path in limits:
            if not path.startswith("/"):
                raise ValueError("body-limit paths must be absolute")
        self._app = app
        self._limits = dict(limits)

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Receive,
        send: Send,
    ) -> None:
        policy = self._policy(scope)
        if policy is None:
            await self._app(scope, receive, send)
            return

        declared_length = _content_length(scope)
        if (
            declared_length is not None
            and declared_length > policy.max_body_bytes
        ):
            await self._send_too_large(send, policy)
            return

        received_bytes = 0

        async def limited_receive() -> ASGIMessage:
            nonlocal received_bytes
            message = await receive()
            if message.get("type") == "http.request":
                received_bytes += len(message.get("body") or b"")
                if received_bytes > policy.max_body_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self._app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            # FastAPI consumes JSON and multipart bodies before invoking these
            # endpoints, so no response has started when the limit is crossed.
            await self._send_too_large(send, policy)

    def _policy(
        self,
        scope: dict[str, Any],
    ) -> RequestBodyLimit | None:
        if (
            scope.get("type") != "http"
            or str(scope.get("method") or "").upper() != "POST"
        ):
            return None
        return self._limits.get(str(scope.get("path") or ""))

    @staticmethod
    async def _send_too_large(
        send: Send,
        policy: RequestBodyLimit,
    ) -> None:
        body = json.dumps(
            {
                "detail": {
                    "code": policy.error_code,
                    "message": policy.message,
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
                "more_body": False,
            }
        )


def _content_length(scope: dict[str, Any]) -> int | None:
    for raw_name, raw_value in scope.get("headers") or ():
        if bytes(raw_name).lower() != b"content-length":
            continue
        try:
            length = int(bytes(raw_value).decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            return None
        return max(0, length)
    return None


__all__ = [
    "AnalysisRequestBodyLimitMiddleware",
    "RequestBodyLimit",
]
