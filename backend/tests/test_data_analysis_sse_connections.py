from __future__ import annotations

import unittest

from scripts.data_analysis_agent.runtime.services.sse_connections import (
    SSEConnectionLimitError,
    SSEConnectionLimiter,
    SSEConnectionLimits,
)


class SSEConnectionLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_per_run_limit_and_idempotent_release(self) -> None:
        limiter = SSEConnectionLimiter(
            SSEConnectionLimits(total=4, per_user=3, per_run=1)
        )
        lease = await limiter.acquire(user_id="user-1", run_id="run-1")

        with self.assertRaises(SSEConnectionLimitError):
            await limiter.acquire(user_id="user-1", run_id="run-1")

        await limiter.release(lease)
        await limiter.release(lease)
        replacement = await limiter.acquire(
            user_id="user-1",
            run_id="run-1",
        )
        self.assertEqual(limiter.active_count, 1)
        await limiter.release(replacement)

    async def test_per_user_and_total_limits_are_independent(self) -> None:
        limiter = SSEConnectionLimiter(
            SSEConnectionLimits(total=3, per_user=2, per_run=1)
        )
        leases = [
            await limiter.acquire(user_id="user-1", run_id="run-1"),
            await limiter.acquire(user_id="user-1", run_id="run-2"),
        ]
        with self.assertRaises(SSEConnectionLimitError):
            await limiter.acquire(user_id="user-1", run_id="run-3")

        leases.append(
            await limiter.acquire(user_id="user-2", run_id="run-3")
        )
        with self.assertRaises(SSEConnectionLimitError):
            await limiter.acquire(user_id="user-3", run_id="run-4")

        for lease in leases:
            await limiter.release(lease)
        self.assertEqual(limiter.active_count, 0)


if __name__ == "__main__":
    unittest.main()
