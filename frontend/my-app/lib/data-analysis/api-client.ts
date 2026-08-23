/**
 * One way to read an analysis API response.
 *
 * Extracted so the run client and the execution client cannot disagree about
 * what a failure looks like. The backend answers with FastAPI's `{detail: ...}`
 * shape, the BFF proxy with `{error: ...}`, and a crash with neither; all three
 * arrive here and leave as one error type.
 *
 * The status code travels with the error because several analysis endpoints use
 * one deliberately: a run that has not executed yet answers 404, and an
 * execution that has not published a result answers 409. Those are states a
 * caller wants to branch on, not failures to surface — and a caller that only
 * has a message string has to match on prose to tell them apart.
 */

export class AnalysisApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "AnalysisApiError";
    this.status = status;
  }

  /** The resource does not exist, or is not this tenant's to see. */
  get isNotFound(): boolean {
    return this.status === 404;
  }

  /** The resource exists but is not in a state that can answer. */
  get isConflict(): boolean {
    return this.status === 409;
  }
}

type ErrorBody = {
  detail?: string | { message?: string };
  error?: string;
};

async function errorMessage(response: Response): Promise<string> {
  const payload = (await response.json().catch(() => null)) as ErrorBody | null;
  if (typeof payload?.detail === "string") return payload.detail;
  if (payload?.detail && typeof payload.detail === "object") {
    return payload.detail.message ?? "Analysis request failed";
  }
  return payload?.error ?? response.statusText ?? "Analysis request failed";
}

/** Parse a successful JSON response, or throw an `AnalysisApiError`. */
export async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new AnalysisApiError(await errorMessage(response), response.status);
  }
  return response.json() as Promise<T>;
}

/**
 * Read a response whose absence is a normal state rather than a failure.
 *
 * Returns `null` for the listed statuses and throws for everything else, so a
 * caller can distinguish "not yet" from "something is wrong" without inspecting
 * an error message.
 */
export async function readOptionalJson<T>(
  response: Response,
  absentStatuses: readonly number[],
): Promise<T | null> {
  if (!response.ok && absentStatuses.includes(response.status)) return null;
  return readJson<T>(response);
}
