/**
 * The shape of a finished execution, mirrored from the backend (Phase 9.14.1).
 *
 * These interfaces track `ExecutionView`, `ExecutionStageView` and the preview
 * models in `backend/apis/analysis_executions.py`. Mirroring by hand across two
 * languages drifts silently — the run type in `analysis-types.ts` had fallen
 * fifteen fields behind before this was written — so
 * `backend/tests/test_analysis_client_contract.py` reads this file and asserts
 * every interface matches the API's published schema.
 *
 * That test parses top-level fields at two-space indent, which is why nothing
 * here uses an inline nested object: each nested shape is its own named type.
 *
 * Deliberately absent, because the API does not send them: the execution key,
 * recipe hash, input signatures, fencing token, worker id and blob references.
 * They are how an execution is addressed and resumed on the server; a client
 * addresses a run.
 */

import type { AnalysisRun } from "@/lib/data-analysis/analysis-types";

export type ExecutionStatus =
  | "reserved"
  | "running"
  | "paused"
  | "succeeded"
  | "failed"
  | "cancelled";

export type ExecutionStageStatus = "pending" | "completed" | "failed";

export type PlanDataType =
  | "string"
  | "integer"
  | "decimal"
  | "currency"
  | "percentage"
  | "date"
  | "datetime"
  | "boolean"
  | "category";

export interface PlanColumn {
  key: string;
  label: string;
  data_type: PlanDataType;
  unit: string | null;
  nullable: boolean;
}

/** Bounded counters. Safe to poll and safe to render; never row data. */
export interface ExecutionMetrics {
  input_rows: number;
  output_rows: number;
  output_columns: number;
  output_bytes: number;
  stages_completed: number;
  stages_reused: number;
  duration_ms: number;
}

/** One logical stage. The checkpoint that lets a worker resume is not sent. */
export interface ExecutionStageView {
  stage_id: string;
  step_ids: string[];
  status: ExecutionStageStatus;
  input_rows: number;
  output_rows: number;
  output_columns: number;
  duration_ms: number;
}

export interface ExecutionView {
  execution_id: string;
  run_id: string;
  plan_id: string;
  plan_hash: string;
  status: ExecutionStatus;
  engine_version: string;
  semantics_version: string;
  current_stage_id: string | null;
  stages: ExecutionStageView[];
  /** True once a result bundle is published, so a preview can be fetched. */
  has_result: boolean;
  result_content_hash: string | null;
  result_columns: PlanColumn[];
  metrics: ExecutionMetrics;
  failure_code: string | null;
  failure_message: string | null;
  warnings: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface ExecutionResponse {
  execution: ExecutionView;
  run: AnalysisRun;
}

/**
 * A bounded sample of the result, redacted when it was published.
 *
 * `rows` is keyed by column key and capped server-side; `truncated` says the
 * result has more rows than the sample shows.
 */
export interface ResultPreview {
  row_count: number;
  preview_row_count: number;
  truncated: boolean;
  privacy_mode: string;
  redacted_column_keys: string[];
  columns: string[];
  rows: Array<Record<string, string | number | boolean | null>>;
}

export interface ExecutionPreviewResponse {
  execution_id: string;
  /** The result's content hash, so a sample can be tied to the result it came from. */
  content_hash: string | null;
  preview: ResultPreview;
}

/** Statuses from which no further execution work will happen. */
const TERMINAL_EXECUTION_STATUSES: ReadonlySet<ExecutionStatus> = new Set([
  "succeeded",
  "failed",
  "cancelled",
]);

export function isExecutionTerminal(execution: ExecutionView): boolean {
  return TERMINAL_EXECUTION_STATUSES.has(execution.status);
}

/**
 * Whether a preview can be fetched for this execution.
 *
 * `has_result` alone is the honest test: the preview endpoint answers 409 for
 * an execution that has not published, so asking first avoids a round trip
 * that is guaranteed to fail.
 */
export function hasReadableResult(execution: ExecutionView): boolean {
  return execution.status === "succeeded" && execution.has_result;
}
