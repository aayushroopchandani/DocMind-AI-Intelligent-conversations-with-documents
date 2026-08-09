export type AnalysisRunStatus =
  | "created"
  | "active"
  | "waiting"
  | "paused"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "expired";

export type AnalysisRunPhase =
  | "context_resolution"
  | "evidence_preparation"
  | "requirements"
  | "normalization"
  | "planning"
  | "plan_validation"
  | "approval"
  | "execution"
  | "result_validation"
  | "proposal"
  | "application"
  | "completed";

export type AnalysisPrivacyMode = "standard" | "schema_only" | "local_only";

export interface PrivacySummary {
  mode: AnalysisPrivacyMode;
  columns_inspected: number;
  sensitive_column_count: number;
  examples_inspected: number;
  examples_redacted: number;
  hidden_rows_excluded: number;
  hidden_columns_excluded: number;
  redacted_column_keys: string[];
  classifications: Record<string, string>;
}

export interface StageTokenUsage {
  stage: string;
  model: string;
  prompt_version: string;
  pricing_version: string;
  pricing_configured: boolean;
  call_count: number;
  duration_ms: number;
  usage: AnalysisRun["token_usage"];
}

export interface AnalysisRun {
  run_id: string;
  workspace_id: string;
  chat_id: string;
  mode: "ask" | "analyse" | "edit";
  prompt: string;
  privacy_mode: AnalysisPrivacyMode;
  privacy_summary: PrivacySummary;
  active_artifact_id: string | null;
  status: AnalysisRunStatus;
  phase: AnalysisRunPhase;
  outcome: string | null;
  inputs_ready: boolean;
  cancellation_requested: boolean;
  pause_requested: boolean;
  paused_at: string | null;
  checkpoint_id: string | null;
  last_completed_step_id: string | null;
  resume_count: number;
  parent_run_id: string | null;
  root_run_id: string | null;
  version: number;
  last_event_sequence: number;
  final_artifact_ids: string[];
  final_dataset_ids: string[];
  current_plan_id: string | null;
  current_plan_revision: number | null;
  current_plan_hash: string | null;
  plan_approval_status: "not_required" | "pending" | "approved" | "rejected" | null;
  warnings_summary: Array<{ code: string; message: string; count: number }>;
  errors_summary: Array<{ code: string; message: string; count: number }>;
  token_usage: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    estimated_cost_usd: number;
  };
  token_usage_by_stage: Record<string, StageTokenUsage>;
  component_versions: Record<string, string>;
  timings_ms: Record<string, number>;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface AnalysisRunEvent {
  event_id: string;
  run_id: string;
  sequence: number;
  event_type: string;
  status: AnalysisRunStatus | null;
  phase: AnalysisRunPhase | null;
  payload: Record<string, unknown>;
  occurred_at: string;
}

export interface PlanDatasetProvenance {
  workbook_id: string | null;
  workbook_revision: number | null;
  worksheet_id: string | null;
  range_a1: string | null;
  snapshot_hash: string | null;
  document_id: string | null;
  page_start: number | null;
  page_end: number | null;
}

export interface AnalysisPlan {
  plan_id: string;
  run_id: string;
  revision: number;
  status: string;
  mode: "ask" | "analyse" | "edit";
  intent: string;
  assumptions: string[];
  input_signature: string;
  input_datasets: Array<{
    alias: string;
    title: string;
    row_count: number;
    columns: Array<{ key: string; label: string; data_type: string; unit: string | null }>;
    provenance: PlanDatasetProvenance[];
  }>;
  steps: Array<{
    step_id: string;
    kind: string;
    depends_on: string[];
    executor: "native" | "python" | "frontend";
    output_alias: string;
    estimate: {
      rows_scanned: number;
      output_rows: number | null;
      cells_written: number;
      duration_seconds: number;
      estimated_cost_usd: number;
    };
    [key: string]: unknown;
  }>;
  write_intents: Array<{
    kind: string;
    intent_id: string;
    destructive?: boolean;
    overwrite_formulas?: boolean;
    target?: {
      workbook_id: string;
      worksheet_id: string;
      base_workbook_revision: number;
      base_snapshot_hash: string;
      placement_policy: string;
    };
  }>;
  expected_artifacts: Array<{ alias: string; kind: string; title: string }>;
  approval_policy: {
    plan_approval_required: boolean;
    plan_approval_reasons: string[];
    final_patch_approval_required: boolean;
    auto_execute_read_only: boolean;
  };
  approval: {
    status: "not_required" | "pending" | "approved" | "rejected";
    comment: string | null;
    rejection_reason: string | null;
  };
  diagnostics: {
    generation_attempt: number;
    repair_count: number;
    validation_warning_count: number;
    validation_error_count: number;
  };
  validator_version: string;
  privacy: PrivacySummary;
  plan_hash: string;
  token_usage: AnalysisRun["token_usage"];
  token_usage_by_stage: Record<string, StageTokenUsage>;
  created_at: string;
  updated_at: string;
}

export interface CreateAnalysisRunResponse {
  created: boolean;
  run_id: string;
  status: AnalysisRunStatus;
  phase: AnalysisRunPhase;
  events_url: string;
  run: AnalysisRun;
}

export interface AnalysisPlanResponse {
  plan: AnalysisPlan;
  run: AnalysisRun;
}

export interface WorkbookVersionGuard {
  workbook_id: string;
  worksheet_id: string;
  workbook_revision: number;
  snapshot_hash: string;
}

export const TERMINAL_RUN_STATUSES = new Set<AnalysisRunStatus>([
  "succeeded",
  "failed",
  "cancelled",
  "expired",
]);
