/**
 * Exercise the execution event fold against a real recorded stream.
 *
 * The fixture below is the durable event stream a real run produced — copied
 * from the backend's own worker narrative, payloads unchanged. If the backend
 * renames a payload field, the fold quietly starts reading `undefined` and the
 * UI shows blanks; this catches that by asserting the folded state and the
 * activity lines, not just that nothing threw.
 *
 * Run with:  npm run verify:execution-events
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, cpSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

/**
 * A real stream, captured from the worker.
 *
 * Regenerate by running an execution through `WorkerAdmissionLifecycleTests`
 * and printing `event.event_type` with `dict(event.payload)`.
 */
const STREAM = [
  { event_type: "run_created", payload: {} },
  { event_type: "run_started", payload: { attempt: 1, recovered: false } },
  { event_type: "context_resolved", payload: { dataset_count: 1, document_count: 0 } },
  { event_type: "planning_started", payload: { mode: "analyse", input_dataset_count: 1 } },
  {
    event_type: "execution_queued",
    payload: { plan_id: "plan-1", revision: 1, step_count: 3, approval_required: false },
  },
  { event_type: "execution_started", payload: { plan_id: "plan-1", step_count: 3 } },
  { event_type: "execution_inputs_resolved", payload: { dataset_count: 2, total_rows: 4200 } },
  {
    event_type: "execution_step_completed",
    payload: {
      step_id: "filter_revenue", kind: "filter_rows", index: 1, total: 3,
      input_rows: 4200, output_rows: 3420, output_columns: 6, removed_rows: 780,
    },
  },
  {
    event_type: "execution_step_completed",
    payload: {
      step_id: "by_region", kind: "aggregate", index: 2, total: 3,
      input_rows: 3420, output_rows: 24, output_columns: 4, removed_rows: 3396,
    },
  },
  {
    event_type: "execution_step_completed",
    payload: {
      step_id: "sorted", kind: "sort_rows", index: 3, total: 3,
      input_rows: 24, output_rows: 24, output_columns: 4, removed_rows: 0,
    },
  },
  { event_type: "result_validation_started", payload: { row_count: 24, column_count: 4 } },
  { event_type: "result_validation_completed", payload: { row_count: 24, column_count: 4 } },
  {
    event_type: "result_materialized",
    payload: { row_count: 24, column_count: 4, byte_count: 4308, content_hash: "6d0a13f5".padEnd(64, "0") },
  },
  {
    event_type: "run_completed",
    payload: {
      plan_id: "plan-1", engine_version: "polars-1.43.2", semantics_version: "2.0",
      isolation: "subprocess", cache_hit: false, row_count: 24,
      content_hash: "6d0a13f5".padEnd(64, "0"),
    },
  },
].map((event, index) => ({
  event_id: `event-${index}`,
  run_id: "run-1",
  sequence: index + 1,
  status: null,
  phase: null,
  occurred_at: "2026-08-24T00:00:00Z",
  ...event,
}));

const EXPECTED_LINES = [
  "Queued for execution",
  "Running 3 steps",
  "Prepared 2 datasets",
  "Filtered to 3,420 rows",
  "Grouped into 24 rows",
  "Sorted 24 rows",
  "Validating the result",
  "Result validated",
  "Saved 24 rows",
  "Finished",
];

/* ------------------------------------------------------------------ */

const workspace = mkdtempSync(join(tmpdir(), "execution-events-"));
cpSync("lib/data-analysis/execution/execution-events.ts", join(workspace, "execution-events.ts"));
// The module formats counts through the shared, locale-pinned helpers.
cpSync("lib/data-analysis/format.ts", join(workspace, "format.ts"));
// The module imports run types only as types, so a stub satisfies the compile
// without dragging the whole app in.
writeFileSync(
  join(workspace, "analysis-types.ts"),
  "export interface AnalysisRunEvent {\n" +
    "  event_id: string; run_id: string; sequence: number; event_type: string;\n" +
    "  status: string | null; phase: string | null;\n" +
    "  payload: Record<string, unknown>; occurred_at: string;\n}\n",
  "utf8",
);

// Rewrite the path alias; tsc here runs without the app's tsconfig paths.
const source = join(workspace, "execution-events.ts");
writeFileSync(
  source,
  readFileSync(source, "utf8")
    .replace("@/lib/data-analysis/analysis-types", "./analysis-types")
    .replace("@/lib/data-analysis/format", "./format"),
  "utf8",
);

execFileSync(
  "./node_modules/.bin/tsc",
  [
    "--target", "es2022",
    "--module", "es2022",
    "--moduleResolution", "bundler",
    "--lib", "es2022,dom",
    "--strict",
    "--outDir", join(workspace, "out"),
    source,
    join(workspace, "analysis-types.ts"),
    join(workspace, "format.ts"),
  ],
  { stdio: "inherit" },
);

// tsc emits extensionless relative imports; Node's ESM loader requires them.
for (const name of readdirSync(join(workspace, "out")).filter((f) => f.endsWith(".js"))) {
  const file = join(workspace, "out", name);
  writeFileSync(
    file,
    readFileSync(file, "utf8").replace(/from "(\.\/[\w.-]+)"/g, (whole, path) =>
      path.endsWith(".js") ? whole : `from "${path}.js"`,
    ),
    "utf8",
  );
}

const {
  IDLE_EXECUTION_PROGRESS,
  foldExecutionEvent,
  foldExecutionEvents,
  describeExecutionEvent,
  parseStepCompleted,
} = await import(pathToFileURL(join(workspace, "out", "execution-events.js")).href);

const failures = [];
let checks = 0;

function check(label, actual, expected) {
  checks += 1;
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) failures.push(`${label}\n  expected: ${e}\n  actual:   ${a}`);
}

/* The folded state after a complete, successful run. */
const final = foldExecutionEvents(STREAM);
check("stage", final.stage, "completed");
check("stepCount", final.stepCount, 3);
check("stepsCompleted", final.stepsCompleted, 3);
check("datasetCount", final.datasetCount, 2);
check("totalInputRows", final.totalInputRows, 4200);
check("resultRowCount", final.resultRowCount, 24);
check("resultColumnCount", final.resultColumnCount, 4);
check("resultByteCount", final.resultByteCount, 4308);
check("contentHash", final.contentHash, "6d0a13f5".padEnd(64, "0"));
check("cacheHit", final.cacheHit, false);
check("failure", final.failure, null);
check("lastStep kind", final.lastStep?.kind, "sort_rows");

/* Plain-language lines, in order. */
check(
  "activity lines",
  STREAM.map(describeExecutionEvent).filter((line) => line !== null),
  EXPECTED_LINES,
);

/* Non-execution events must not disturb the state, by identity. */
checks += 1;
const before = foldExecutionEvents(STREAM.slice(0, 6));
const after = foldExecutionEvent(before, {
  ...STREAM[0],
  event_type: "dataset_registered",
  payload: { dataset_id: "d1" },
});
if (before !== after) {
  failures.push("a non-execution event returned a new object; React would re-render for nothing");
}

/* Malformed payloads degrade rather than throw. */
checks += 1;
try {
  const broken = foldExecutionEvent(IDLE_EXECUTION_PROGRESS, {
    ...STREAM[0],
    event_type: "execution_step_completed",
    payload: { step_id: 42, kind: null, index: "first" },
  });
  if (broken.lastStep !== null) failures.push("a malformed step payload was accepted");
  if (describeExecutionEvent({ ...STREAM[0], event_type: "execution_step_completed", payload: {} }) !== null) {
    failures.push("a malformed step payload produced an activity line");
  }
} catch (error) {
  failures.push(`a malformed payload threw instead of degrading: ${error.message}`);
}

/* Replay must converge, not double-count. */
checks += 1;
const replayed = foldExecutionEvents([...STREAM, ...STREAM]);
if (replayed.stepsCompleted !== 3) {
  failures.push(`replaying the stream counted ${replayed.stepsCompleted} steps instead of 3`);
}

/* A failure is carried with its typed code. */
checks += 1;
const failed = foldExecutionEvent(IDLE_EXECUTION_PROGRESS, {
  ...STREAM[0],
  event_type: "run_failed",
  payload: { code: "input_unavailable", message: "dataset version is gone", retryable: false },
});
if (failed.stage !== "failed" || failed.failure?.code !== "input_unavailable") {
  failures.push(`a failure event did not carry its code: ${JSON.stringify(failed.failure)}`);
}

/* An unknown operation kind still says something useful. */
checks += 1;
const unknown = describeExecutionEvent({
  ...STREAM[0],
  event_type: "execution_step_completed",
  payload: { step_id: "s", kind: "cluster_rows", index: 1, total: 1, output_rows: 5 },
});
if (unknown !== "Ran cluster rows — 5 rows") {
  failures.push(`an unknown step kind produced: ${JSON.stringify(unknown)}`);
}

/* parseStepCompleted only accepts its own event. */
checks += 1;
if (parseStepCompleted(STREAM[0]) !== null) {
  failures.push("parseStepCompleted accepted an unrelated event");
}

for (const message of failures) console.error(message);

if (failures.length > 0) {
  console.error(`\n${failures.length} of ${checks} execution-event checks failed.`);
  process.exit(1);
}

console.log(`All ${checks} execution-event checks passed against a recorded run.`);
