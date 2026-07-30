from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from apis.deps import verify_internal_secret


class InternalAuthenticationDependencyTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_missing_server_secret_fails_closed(self) -> None:
        with patch(
            "apis.deps.settings",
            SimpleNamespace(internal_api_secret=""),
        ):
            with self.assertRaises(HTTPException) as raised:
                await verify_internal_secret("attacker-controlled")

        self.assertEqual(raised.exception.status_code, 503)

    async def test_secret_must_match_exactly(self) -> None:
        with patch(
            "apis.deps.settings",
            SimpleNamespace(internal_api_secret="server-secret"),
        ):
            with self.assertRaises(HTTPException) as raised:
                await verify_internal_secret("wrong-secret")
            await verify_internal_secret("server-secret")

        self.assertEqual(raised.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
