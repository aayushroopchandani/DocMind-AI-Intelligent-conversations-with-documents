/**
 * Assert the browser's analysis-run allowlist matches the backend's surface.
 *
 * The table in `lib/server/analysis-routes.ts` is a tenant boundary: anything
 * it resolves is reachable by any signed-in session, and anything it does not
 * resolve does not exist as far as the browser is concerned. Both halves of
 * that need checking, so this script asserts two directions:
 *
 *   reachable   — every backend route the browser needs resolves, to exactly
 *                 the path the backend publishes, with the right body handling;
 *   unreachable — malformed ids, wrong methods, unknown paths and attempts to
 *                 steer the backend URL all resolve to nothing.
 *
 * The backend half of this pairing lives in
 * `backend/tests/test_analysis_browser_surface.py`, which asserts the FastAPI
 * app publishes exactly the routes listed here. If someone adds an endpoint,
 * that test fails and points at this file.
 *
 * Run with:  npm run verify:analysis-routes
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, cpSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const RUN = "3fa85f64-5717-4562-b3fc-2c963f66afa6";
const PATCH = "9f8e7d6c-5b4a-4938-8271-0a1b2c3d4e5f";

/** Every route the browser must be able to reach, and how its body travels. */
const REACHABLE = [
  { method: "GET", path: [RUN], backend: `/analysis/runs/${RUN}`, kind: "json" },
  { method: "GET", path: [RUN, "events"], backend: `/analysis/runs/${RUN}/events`, kind: "stream", query: true },
  { method: "GET", path: [RUN, "plan"], backend: `/analysis/runs/${RUN}/plan`, kind: "json" },
  { method: "GET", path: [RUN, "execution"], backend: `/analysis/runs/${RUN}/execution`, kind: "json" },
  { method: "GET", path: [RUN, "execution", "preview"], backend: `/analysis/runs/${RUN}/execution/preview`, kind: "json" },
  { method: "GET", path: [RUN, "patch"], backend: `/analysis/runs/${RUN}/patch`, kind: "json" },
  { method: "POST", path: [RUN, "approve"], backend: `/analysis/runs/${RUN}/approve`, kind: "json" },
  { method: "POST", path: [RUN, "reject"], backend: `/analysis/runs/${RUN}/reject`, kind: "json" },
  { method: "POST", path: [RUN, "cancel"], backend: `/analysis/runs/${RUN}/cancel`, kind: "json" },
  { method: "POST", path: [RUN, "pause"], backend: `/analysis/runs/${RUN}/pause`, kind: "json" },
  { method: "POST", path: [RUN, "resume"], backend: `/analysis/runs/${RUN}/resume`, kind: "json" },
  { method: "POST", path: [RUN, "resume-as-new"], backend: `/analysis/runs/${RUN}/resume-as-new`, kind: "json" },
  { method: "POST", path: [RUN, "patch", "context"], backend: `/analysis/runs/${RUN}/patch/context`, kind: "json" },
  { method: "POST", path: [RUN, "patch", "approve"], backend: `/analysis/runs/${RUN}/patch/approve`, kind: "json" },
  { method: "POST", path: [RUN, "patch", "reject"], backend: `/analysis/runs/${RUN}/patch/reject`, kind: "json" },
  { method: "POST", path: [RUN, "patch", "preflight"], backend: `/analysis/runs/${RUN}/patch/preflight`, kind: "json" },
  { method: "POST", path: [RUN, "patch", "receipt"], backend: `/analysis/runs/${RUN}/patch/receipt`, kind: "json" },
  { method: "POST", path: [RUN, "patch", "undo"], backend: `/analysis/runs/${RUN}/patch/undo`, kind: "json" },
  {
    method: "GET",
    path: [RUN, "patch", PATCH, "revisions", "2", "operations", "write_result", "chunks", "0"],
    backend: `/analysis/runs/${RUN}/patch/${PATCH}/revisions/2/operations/write_result/chunks/0`,
    kind: "binary",
  },
];

/** Operation ids the compiler really produces must all resolve. */
const REAL_OP_IDS = [
  "write_result",
  "create_target_sheet",
  "formula_revenue_total",
  "write_result__inverse",
  "_leading_underscore",
];

const UNREACHABLE = [
  { why: "empty path", method: "GET", path: [] },
  { why: "run id is not a uuid", method: "GET", path: ["not-a-uuid", "plan"] },
  { why: "run id is a traversal attempt", method: "GET", path: ["..", "plan"] },
  { why: "unknown operation", method: "GET", path: [RUN, "secrets"] },
  { why: "unknown nested operation", method: "POST", path: [RUN, "patch", "delete"] },
  { why: "read-only route requested as a write", method: "POST", path: [RUN, "plan"] },
  { why: "write route requested as a read", method: "GET", path: [RUN, "cancel"] },
  { why: "bare run id as a write", method: "POST", path: [RUN] },
  { why: "traversal in a trailing segment", method: "GET", path: [RUN, ".."] },
  { why: "traversal toward another tenant", method: "GET", path: [RUN, "..", "..", "diagnostics"] },
  { why: "encoded separator in a segment", method: "GET", path: [RUN, "patch/context"] },
  { why: "diagnostics is never proxied", method: "GET", path: [RUN, "diagnostics"] },
  { why: "chunk patch id is not a uuid", method: "GET", path: [RUN, "patch", "abc", "revisions", "1", "operations", "write_result", "chunks", "0"] },
  { why: "chunk revision is zero", method: "GET", path: [RUN, "patch", PATCH, "revisions", "0", "operations", "write_result", "chunks", "0"] },
  { why: "chunk revision is negative", method: "GET", path: [RUN, "patch", PATCH, "revisions", "-1", "operations", "write_result", "chunks", "0"] },
  { why: "chunk index is not a number", method: "GET", path: [RUN, "patch", PATCH, "revisions", "1", "operations", "write_result", "chunks", "x"] },
  { why: "chunk index has a leading zero", method: "GET", path: [RUN, "patch", PATCH, "revisions", "1", "operations", "write_result", "chunks", "01"] },
  { why: "operation id carries a separator", method: "GET", path: [RUN, "patch", PATCH, "revisions", "1", "operations", "a/b", "chunks", "0"] },
  { why: "operation id carries a query", method: "GET", path: [RUN, "patch", PATCH, "revisions", "1", "operations", "a?b", "chunks", "0"] },
  { why: "operation id starts with a digit", method: "GET", path: [RUN, "patch", PATCH, "revisions", "1", "operations", "1bad", "chunks", "0"] },
  { why: "chunk path is truncated", method: "GET", path: [RUN, "patch", PATCH, "revisions", "1", "operations", "write_result"] },
];

/* ------------------------------------------------------------------ */

const workspace = mkdtempSync(join(tmpdir(), "analysis-routes-"));
cpSync("lib/server/analysis-routes.ts", join(workspace, "analysis-routes.ts"));

execFileSync(
  "./node_modules/.bin/tsc",
  [
    "--target", "es2022",
    "--module", "es2022",
    "--moduleResolution", "bundler",
    // `dom` only to satisfy the ambient @types tsc picks up from
    // node_modules; the route table itself uses nothing from it.
    "--lib", "es2022,dom",
    "--outDir", join(workspace, "out"),
    join(workspace, "analysis-routes.ts"),
  ],
  { stdio: "inherit" },
);

const { resolveAnalysisRunRoute } = await import(
  pathToFileURL(join(workspace, "out", "analysis-routes.js")).href
);

const failures = [];
let checks = 0;

function fail(message) {
  failures.push(message);
}

for (const route of REACHABLE) {
  checks += 1;
  const resolved = resolveAnalysisRunRoute(route.path, route.method);
  if (!resolved) {
    fail(`UNREACHABLE ${route.method} /${route.path.join("/")} — expected ${route.backend}`);
    continue;
  }
  if (resolved.backendPath !== route.backend) {
    fail(`WRONG PATH ${route.method} /${route.path.join("/")}\n  expected: ${route.backend}\n  resolved: ${resolved.backendPath}`);
  }
  if (resolved.kind !== route.kind) {
    fail(`WRONG BODY HANDLING ${route.method} ${route.backend}\n  expected: ${route.kind}\n  resolved: ${resolved.kind}`);
  }
  if (resolved.forwardQuery !== (route.query === true)) {
    fail(`WRONG QUERY POLICY ${route.method} ${route.backend} — forwardQuery=${resolved.forwardQuery}`);
  }
}

for (const opId of REAL_OP_IDS) {
  checks += 1;
  const path = [RUN, "patch", PATCH, "revisions", "1", "operations", opId, "chunks", "0"];
  const resolved = resolveAnalysisRunRoute(path, "GET");
  if (!resolved) fail(`UNREACHABLE chunk for a real operation id: ${opId}`);
}

for (const probe of UNREACHABLE) {
  checks += 1;
  const resolved = resolveAnalysisRunRoute(probe.path, probe.method);
  if (resolved) {
    fail(`LEAK (${probe.why}) ${probe.method} /${probe.path.join("/")} → ${resolved.backendPath}`);
  }
}

// Whatever a route resolves to must stay inside this run's own namespace.
for (const route of REACHABLE) {
  checks += 1;
  const resolved = resolveAnalysisRunRoute(route.path, route.method);
  const prefix = `/analysis/runs/${RUN}`;
  if (!resolved || (resolved.backendPath !== prefix && !resolved.backendPath.startsWith(`${prefix}/`))) {
    fail(`ESCAPED NAMESPACE ${route.method} ${resolved?.backendPath ?? "(null)"}`);
  }
  if (resolved && (resolved.backendPath.includes("..") || resolved.backendPath.includes("//"))) {
    fail(`SUSPICIOUS PATH ${resolved.backendPath}`);
  }
}

for (const message of failures) console.error(message);

if (failures.length > 0) {
  console.error(`\n${failures.length} of ${checks} analysis-route checks failed.`);
  process.exit(1);
}

console.log(`All ${checks} analysis-route checks passed (${REACHABLE.length} routes reachable, ${UNREACHABLE.length} refusals).`);
