/**
 * Turning the run event stream into execution progress (Phase 9.13.2).
 *
 * The durable stream carries `payload: Record<string, unknown>` — it is JSON
 * that crossed a network, so nothing here trusts its shape. Every field is read
 * through a guard that returns `null` rather than coercing, and an event whose
 * payload is malformed degrades to less detail instead of throwing inside a
 * render.
 *
 * The fold is deliberately incremental and identity-stable: an event that
 * changes nothing returns the very same object, so a React consumer holding
 * this state re-renders only when something actually moved. That matters
 * because most events on the stream belong to other phases entirely.
 *
 * The backend guarantees these payloads carry identifiers, counts and durations
 * and never row data (9.14.2), which is why this module can render payload
 * values directly.
 */

import type { AnalysisRunEvent } from "@/lib/data-analysis/analysis-types";

/* ------------------------------------------------------------------ */
/* Payload readers                                                      */
/* ------------------------------------------------------------------ */

type Payload = Record<string, unknown>;

function readNumber(payload: Payload, key: string): number | null {
  const value = payload[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readString(payload: Payload, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readBoolean(payload: Payload, key: string): boolean {
  return payload[key] === true;
}

/* ------------------------------------------------------------------ */
/* Typed events                                                         */
/* ------------------------------------------------------------------ */

/** Native operations the planner may emit, from the capability profile. */
export type NativeOperationKind =
  | "generate_dataset"
  | "filter_rows"
  | "sort_rows"
  | "select_columns"
  | "rename_columns"
  | "fill_missing"
  | "deduplicate"
  | "derive_column"
  | "aggregate"
  | "join"
  | "pivot"
  | "unpivot"
  | "compose_response";

export interface ExecutionStepCompleted {
  step_id: string;
  kind: string;
  index: number;
  total: number;
  input_rows: number;
  output_rows: number;
  output_columns: number;
  removed_rows: number;
}

/** Where an execution has got to, as far as the stream has reported. */
export type ExecutionStage =
  | "queued"
  | "running"
  | "validating"
  | "publishing"
  | "completed"
  | "failed";

export interface ExecutionFailure {
  code: string;
  message: string | null;
  retryable: boolean;
}

export interface ExecutionProgress {
  readonly stage: ExecutionStage | null;
  readonly stepCount: number | null;
  readonly stepsCompleted: number;
  readonly datasetCount: number | null;
  readonly totalInputRows: number | null;
  readonly lastStep: ExecutionStepCompleted | null;
  readonly resultRowCount: number | null;
  readonly resultColumnCount: number | null;
  readonly resultByteCount: number | null;
  readonly contentHash: string | null;
  readonly cacheHit: boolean;
  readonly failure: ExecutionFailure | null;
}

export const IDLE_EXECUTION_PROGRESS: ExecutionProgress = {
  stage: null,
  stepCount: null,
  stepsCompleted: 0,
  datasetCount: null,
  totalInputRows: null,
  lastStep: null,
  resultRowCount: null,
  resultColumnCount: null,
  resultByteCount: null,
  contentHash: null,
  cacheHit: false,
  failure: null,
};

/**
 * Event types this module folds. Anything else leaves the state untouched.
 *
 * Exported so a consumer can filter the stream before folding when it wants to
 * — the fold is safe either way.
 */
export const EXECUTION_EVENT_TYPES: ReadonlySet<string> = new Set([
  "execution_queued",
  "execution_started",
  "execution_inputs_resolved",
  "execution_step_completed",
  "result_validation_started",
  "result_validation_completed",
  "result_materialized",
  "run_completed",
  "run_failed",
]);

export function isExecutionEvent(event: AnalysisRunEvent): boolean {
  return EXECUTION_EVENT_TYPES.has(event.event_type);
}

/** Parse a step-completed payload, or `null` if it is not one. */
export function parseStepCompleted(
  event: AnalysisRunEvent,
): ExecutionStepCompleted | null {
  if (event.event_type !== "execution_step_completed") return null;
  const payload = event.payload;
  const stepId = readString(payload, "step_id");
  const kind = readString(payload, "kind");
  const index = readNumber(payload, "index");
  const total = readNumber(payload, "total");
  if (stepId === null || kind === null || index === null || total === null) {
    return null;
  }
  return {
    step_id: stepId,
    kind,
    index,
    total,
    input_rows: readNumber(payload, "input_rows") ?? 0,
    output_rows: readNumber(payload, "output_rows") ?? 0,
    output_columns: readNumber(payload, "output_columns") ?? 0,
    removed_rows: readNumber(payload, "removed_rows") ?? 0,
  };
}

/* ------------------------------------------------------------------ */
/* The fold                                                             */
/* ------------------------------------------------------------------ */

/**
 * Apply one event to the progress state.
 *
 * Returns `state` unchanged — by identity, not just by value — for any event
 * that does not belong to an execution, which is most of them.
 */
export function foldExecutionEvent(
  state: ExecutionProgress,
  event: AnalysisRunEvent,
): ExecutionProgress {
  const payload = event.payload;

  switch (event.event_type) {
    case "execution_queued":
      return {
        ...state,
        stage: "queued",
        stepCount: readNumber(payload, "step_count") ?? state.stepCount,
      };

    case "execution_started":
      return {
        ...state,
        stage: "running",
        stepCount: readNumber(payload, "step_count") ?? state.stepCount,
      };

    case "execution_inputs_resolved":
      return {
        ...state,
        stage: "running",
        datasetCount: readNumber(payload, "dataset_count"),
        totalInputRows: readNumber(payload, "total_rows"),
      };

    case "execution_step_completed": {
      const step = parseStepCompleted(event);
      if (step === null) return state;
      return {
        ...state,
        stage: "running",
        lastStep: step,
        // The event carries its own position, so a replayed or out-of-order
        // stream converges on the furthest step rather than counting arrivals.
        stepsCompleted: Math.max(state.stepsCompleted, step.index),
        stepCount: step.total,
      };
    }

    case "result_validation_started":
      return {
        ...state,
        stage: "validating",
        resultRowCount: readNumber(payload, "row_count"),
        resultColumnCount: readNumber(payload, "column_count"),
      };

    case "result_validation_completed":
      return {
        ...state,
        stage: "publishing",
        resultRowCount:
          readNumber(payload, "row_count") ?? state.resultRowCount,
        resultColumnCount:
          readNumber(payload, "column_count") ?? state.resultColumnCount,
      };

    case "result_materialized":
      return {
        ...state,
        stage: "publishing",
        resultRowCount:
          readNumber(payload, "row_count") ?? state.resultRowCount,
        resultColumnCount:
          readNumber(payload, "column_count") ?? state.resultColumnCount,
        resultByteCount: readNumber(payload, "byte_count"),
        contentHash: readString(payload, "content_hash"),
      };

    case "run_completed":
      return {
        ...state,
        stage: "completed",
        cacheHit: readBoolean(payload, "cache_hit"),
        resultRowCount:
          readNumber(payload, "row_count") ?? state.resultRowCount,
        contentHash:
          readString(payload, "content_hash") ?? state.contentHash,
      };

    case "run_failed": {
      const code = readString(payload, "code");
      if (code === null) return { ...state, stage: "failed" };
      return {
        ...state,
        stage: "failed",
        failure: {
          code,
          message: readString(payload, "message"),
          retryable: readBoolean(payload, "retryable"),
        },
      };
    }

    default:
      // Not an execution event. Same object back, so React can bail out.
      return state;
  }
}

/** Fold a whole stream. Useful after an SSE replay or when opening a run. */
export function foldExecutionEvents(
  events: readonly AnalysisRunEvent[],
  initial: ExecutionProgress = IDLE_EXECUTION_PROGRESS,
): ExecutionProgress {
  let state = initial;
  for (const event of events) state = foldExecutionEvent(state, event);
  return state;
}

/* ------------------------------------------------------------------ */
/* Plain language                                                       */
/* ------------------------------------------------------------------ */

/**
 * What each native operation did, in the past tense.
 *
 * Phrased around the number that matters for the operation: a filter is about
 * how many rows survived, an aggregate about how many groups came out, a join
 * about the shape of the result.
 */
const STEP_VERBS: Record<string, string> = {
  generate_dataset: "Generated",
  filter_rows: "Filtered to",
  sort_rows: "Sorted",
  select_columns: "Selected",
  rename_columns: "Renamed columns on",
  fill_missing: "Filled missing values in",
  deduplicate: "Deduplicated to",
  derive_column: "Derived a column across",
  aggregate: "Grouped into",
  join: "Joined into",
  pivot: "Pivoted into",
  unpivot: "Unpivoted into",
  compose_response: "Composed",
};

function rows(count: number): string {
  return `${count.toLocaleString()} ${count === 1 ? "row" : "rows"}`;
}

/**
 * One line of activity for an event, or `null` if it has nothing to say.
 *
 * Kept here rather than in a component so the wording is testable and the
 * component stays a renderer.
 */
export function describeExecutionEvent(
  event: AnalysisRunEvent,
): string | null {
  const payload = event.payload;

  switch (event.event_type) {
    case "execution_queued":
      return "Queued for execution";

    case "execution_started": {
      const steps = readNumber(payload, "step_count");
      if (steps === null) return "Running";
      return `Running ${steps} ${steps === 1 ? "step" : "steps"}`;
    }

    case "execution_inputs_resolved": {
      const datasets = readNumber(payload, "dataset_count");
      if (datasets === null) return "Prepared the inputs";
      return `Prepared ${datasets} ${datasets === 1 ? "dataset" : "datasets"}`;
    }

    case "execution_step_completed": {
      const step = parseStepCompleted(event);
      if (step === null) return null;
      const verb = STEP_VERBS[step.kind];
      if (verb === undefined) {
        return `Ran ${step.kind.replaceAll("_", " ")} — ${rows(step.output_rows)}`;
      }
      return `${verb} ${rows(step.output_rows)}`;
    }

    case "result_validation_started":
      return "Validating the result";

    case "result_validation_completed":
      return "Result validated";

    case "result_materialized": {
      const count = readNumber(payload, "row_count");
      return count === null ? "Result saved" : `Saved ${rows(count)}`;
    }

    case "run_completed":
      return readBoolean(payload, "cache_hit")
        ? "Reused an identical earlier result"
        : "Finished";

    case "run_failed":
      return readString(payload, "message") ?? "Execution failed";

    default:
      return null;
  }
}
