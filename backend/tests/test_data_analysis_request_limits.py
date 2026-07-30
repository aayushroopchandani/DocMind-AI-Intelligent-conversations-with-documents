from __future__ import annotations

import json
import unittest
from collections.abc import Awaitable, Callable
from typing import Any

from scripts.data_analysis_agent.runtime.http import (
    AnalysisRequestBodyLimitMiddleware,
    RequestBodyLimit,
)


class _ReadingApp:
    def __init__(self) -> None:
        self.called = False
        self.bytes_read = 0

    async def __call__(
        self,
        _scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.called = True
        while True:
            message = await receive()
            self.bytes_read += len(message.get("body") or b"")
            if not message.get("more_body", False):
                break
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"",
                "more_body": False,
            }
        )


def _scope(
    *,
    path: str = "/analysis/runs",
    method: str = "POST",
    content_length: int | None = None,
) -> dict[str, Any]:
    headers = (
        [(b"content-length", str(content_length).encode("ascii"))]
        if content_length is not None
        else []
    )
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": method,
        "path": path,
        "headers": headers,
    }


class AnalysisRunBodyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def _invoke(
        self,
        *,
        app: _ReadingApp,
        scope: dict[str, Any],
        messages: list[dict[str, Any]],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        pending = iter(messages)
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return next(pending)

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        middleware = AnalysisRequestBodyLimitMiddleware(
            app,
            limits={
                "/analysis/runs": RequestBodyLimit(
                    max_body_bytes=limit,
                    error_code="analysis_request_too_large",
                    message="Analysis run request is too large.",
                ),
                "/analysis/artifacts": RequestBodyLimit(
                    max_body_bytes=limit + 5,
                    error_code="analysis_artifact_request_too_large",
                    message="Analysis artifact request is too large.",
                ),
            },
        )
        await middleware(scope, receive, send)
        return sent

    async def test_content_length_rejects_before_reading_body(self) -> None:
        app = _ReadingApp()
        sent = await self._invoke(
            app=app,
            scope=_scope(content_length=11),
            messages=[],
        )

        self.assertFalse(app.called)
        self.assertEqual(sent[0]["status"], 413)
        payload = json.loads(sent[1]["body"])
        self.assertEqual(
            payload["detail"]["code"],
            "analysis_request_too_large",
        )

    async def test_chunked_body_cannot_bypass_limit(self) -> None:
        app = _ReadingApp()
        sent = await self._invoke(
            app=app,
            scope=_scope(),
            messages=[
                {
                    "type": "http.request",
                    "body": b"123456",
                    "more_body": True,
                },
                {
                    "type": "http.request",
                    "body": b"78901",
                    "more_body": False,
                },
            ],
        )

        self.assertTrue(app.called)
        self.assertEqual(sent[0]["status"], 413)

    async def test_other_routes_are_not_limited(self) -> None:
        app = _ReadingApp()
        sent = await self._invoke(
            app=app,
            scope=_scope(path="/upload", content_length=11),
            messages=[
                {
                    "type": "http.request",
                    "body": b"12345678901",
                    "more_body": False,
                }
            ],
        )

        self.assertTrue(app.called)
        self.assertEqual(app.bytes_read, 11)
        self.assertEqual(sent[0]["status"], 204)

    async def test_multipart_artifact_is_limited_before_parsing(self) -> None:
        app = _ReadingApp()
        sent = await self._invoke(
            app=app,
            scope=_scope(
                path="/analysis/artifacts",
                content_length=16,
            ),
            messages=[],
        )

        self.assertFalse(app.called)
        self.assertEqual(sent[0]["status"], 413)
        payload = json.loads(sent[1]["body"])
        self.assertEqual(
            payload["detail"]["code"],
            "analysis_artifact_request_too_large",
        )


if __name__ == "__main__":
    unittest.main()
