/**
 * Reading what a run executed (Phase 9.14.1).
 *
 * Two calls, split the way the API splits them. The metadata call answers from
 * MongoDB alone, so it is safe to poll while a run is in flight; the preview
 * call spends a blob download, so it is made once, after the metadata says a
 * result exists.
 *
 * Both treat "not yet" as a value rather than an error. A run that is still
 * planning has no execution (404), and an execution that has not published has
 * no preview (409). Those are ordinary points in a run's life, and a caller
 * that had to catch and inspect an error to notice them would be reading prose
 * to make a control-flow decision.
 */

import { readOptionalJson } from "@/lib/data-analysis/api-client";
import type {
  ExecutionPreviewResponse,
  ExecutionResponse,
} from "@/lib/data-analysis/execution/execution-types";

/** The run has no execution record yet. */
const NO_EXECUTION = [404] as const;

/** No execution, or one that has not published a result to sample. */
const NO_PUBLISHED_RESULT = [404, 409] as const;

function executionUrl(runId: string, suffix = ""): string {
  return `/api/analysis/runs/${encodeURIComponent(runId)}/execution${suffix}`;
}

/**
 * Fetch what this run executed, or `null` if it has not executed yet.
 *
 * Cheap enough to call on every execution event: it reads one durable record
 * and touches no blob storage.
 */
export async function getRunExecution(
  runId: string,
  options: { signal?: AbortSignal } = {},
): Promise<ExecutionResponse | null> {
  const response = await fetch(executionUrl(runId), {
    cache: "no-store",
    signal: options.signal,
  });
  return readOptionalJson<ExecutionResponse>(response, NO_EXECUTION);
}

/**
 * Fetch the bounded sample of this run's result, or `null` if there is none.
 *
 * The sample was bounded and redacted when the result was published, so what
 * arrives here is already safe to render. Call it once a
 * `getRunExecution` result reports `has_result`; calling earlier is not an
 * error, it just returns `null`.
 */
export async function getRunExecutionPreview(
  runId: string,
  options: { signal?: AbortSignal } = {},
): Promise<ExecutionPreviewResponse | null> {
  const response = await fetch(executionUrl(runId, "/preview"), {
    cache: "no-store",
    signal: options.signal,
  });
  return readOptionalJson<ExecutionPreviewResponse>(
    response,
    NO_PUBLISHED_RESULT,
  );
}
