"""What the browser is allowed to reach, pinned on the backend side.

The Next.js proxy in `frontend/my-app/app/api/analysis/runs/[...path]/route.ts`
holds an allowlist of analysis-run routes. Anything on it is reachable by any
signed-in session; anything off it does not exist as far as the browser is
concerned. That list is maintained by hand, in another language, in another
repository directory — so it can silently fall behind this app.

This test is the other half of that pairing. It asserts the FastAPI app
publishes exactly the run routes the proxy knows about. Adding an endpoint
therefore fails here, with a message naming the file to update, rather than
shipping an endpoint the browser cannot call — or, worse, quietly widening what
a session can reach.

The frontend half is `frontend/my-app/scripts/verify-analysis-routes.mjs`
(`npm run verify:analysis-routes`).
"""

from __future__ import annotations

import unittest

import main


PROXY_TABLE = (
    "frontend/my-app/lib/server/analysis-routes.ts"
)

#: Every `/analysis/runs/...` route the proxy's table resolves, as
#: (method, path template). Keep in step with ANALYSIS_RUN_ROUTES.
BROWSER_REACHABLE_RUN_ROUTES = frozenset(
    {
        # Run lifecycle (Phase 8).
        ("GET", "/analysis/runs/{run_id}"),
        ("GET", "/analysis/runs/{run_id}/events"),
        ("GET", "/analysis/runs/{run_id}/plan"),
        ("POST", "/analysis/runs/{run_id}/approve"),
        ("POST", "/analysis/runs/{run_id}/reject"),
        ("POST", "/analysis/runs/{run_id}/cancel"),
        ("POST", "/analysis/runs/{run_id}/pause"),
        ("POST", "/analysis/runs/{run_id}/resume"),
        ("POST", "/analysis/runs/{run_id}/resume-as-new"),
        # Execution reads (Phase 9.14.1).
        ("GET", "/analysis/runs/{run_id}/execution"),
        ("GET", "/analysis/runs/{run_id}/execution/preview"),
        # Workbook patch lifecycle (Phase 9.11–9.12).
        ("GET", "/analysis/runs/{run_id}/patch"),
        ("POST", "/analysis/runs/{run_id}/patch/context"),
        ("POST", "/analysis/runs/{run_id}/patch/approve"),
        ("POST", "/analysis/runs/{run_id}/patch/reject"),
        ("POST", "/analysis/runs/{run_id}/patch/preflight"),
        ("POST", "/analysis/runs/{run_id}/patch/receipt"),
        ("POST", "/analysis/runs/{run_id}/patch/undo"),
        (
            "GET",
            "/analysis/runs/{run_id}/patch/{patch_id}/revisions/{revision}"
            "/operations/{op_id}/chunks/{index}",
        ),
    }
)

#: Analysis routes the browser reaches through their own proxy handlers, not
#: through the run catch-all. Listed so the assertion below can exclude them
#: deliberately rather than by accident.
SEPARATELY_PROXIED = frozenset(
    {
        ("GET", "/analysis/runs"),
        ("POST", "/analysis/runs"),
        ("POST", "/analysis/artifacts"),
        ("GET", "/analysis/artifacts/versions/{version_id}/download-url"),
        ("POST", "/analysis/spreadsheets/import"),
        ("POST", "/analysis/spreadsheets/export"),
    }
)

#: Operator endpoints. These must never gain a browser proxy: diagnostics
#: exposes worker and queue internals across the whole deployment.
OPERATOR_ONLY = frozenset(
    {
        ("GET", "/analysis/diagnostics"),
        ("GET", "/analysis/health"),
        ("GET", "/analysis/ready"),
    }
)


def _published_analysis_routes() -> set[tuple[str, str]]:
    """Every analysis route the app publishes, as (method, path template)."""

    schema = main.app.openapi()
    routes: set[tuple[str, str]] = set()
    for path, operations in schema["paths"].items():
        if not path.startswith("/analysis/"):
            continue
        for method in operations:
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                routes.add((method.upper(), path))
    return routes


class BrowserSurfaceTests(unittest.TestCase):
    def test_run_routes_match_the_proxy_allowlist(self) -> None:
        published = {
            route
            for route in _published_analysis_routes()
            if route[1].startswith("/analysis/runs/{run_id}")
        }

        missing_from_proxy = published - BROWSER_REACHABLE_RUN_ROUTES
        self.assertFalse(
            missing_from_proxy,
            "These run routes are published but the browser proxy cannot "
            f"reach them. Add them to {PROXY_TABLE} and to this test, or "
            "confirm deliberately that they are server-only: "
            + ", ".join(f"{m} {p}" for m, p in sorted(missing_from_proxy)),
        )

        stale_in_proxy = BROWSER_REACHABLE_RUN_ROUTES - published
        self.assertFalse(
            stale_in_proxy,
            "The browser proxy allows run routes this app no longer "
            f"publishes. Remove them from {PROXY_TABLE}: "
            + ", ".join(f"{m} {p}" for m, p in sorted(stale_in_proxy)),
        )

    def test_every_analysis_route_is_accounted_for(self) -> None:
        """No analysis route may exist without a decision about the browser."""

        published = _published_analysis_routes()
        classified = (
            BROWSER_REACHABLE_RUN_ROUTES | SEPARATELY_PROXIED | OPERATOR_ONLY
        )
        unclassified = published - classified

        self.assertFalse(
            unclassified,
            "These analysis routes are neither proxied to the browser nor "
            "declared operator-only. Decide which, then record it in this "
            "test: " + ", ".join(f"{m} {p}" for m, p in sorted(unclassified)),
        )

    def test_operator_endpoints_are_not_browser_reachable(self) -> None:
        """Diagnostics must not be expressible through the run catch-all."""

        for _method, path in OPERATOR_ONLY:
            self.assertFalse(
                path.startswith("/analysis/runs/{run_id}"),
                f"{path} sits under the run prefix, so the catch-all proxy "
                "could be made to express it",
            )

    def test_one_route_takes_client_values_beyond_the_run_id(self) -> None:
        """Every placeholder is a value the browser chooses, so each one needs
        its own charset check in the proxy. Knowing there is exactly one route
        with more than the run id keeps that surface reviewable."""

        multi_parameter = {
            path
            for _method, path in BROWSER_REACHABLE_RUN_ROUTES
            if path.count("{") > 1
        }
        self.assertEqual(len(multi_parameter), 1)
        chunk_route = next(iter(multi_parameter))
        # patch_id, revision, op_id and index, on top of run_id.
        self.assertEqual(chunk_route.count("{"), 5)
        self.assertIn("chunks", chunk_route)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
