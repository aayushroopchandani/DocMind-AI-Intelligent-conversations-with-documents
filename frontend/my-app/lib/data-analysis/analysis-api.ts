import type {
  AnalysisPlanResponse,
  AnalysisRun,
  AnalysisRunEvent,
  CreateAnalysisRunResponse,
  WorkbookVersionGuard,
} from "@/lib/data-analysis/analysis-types";

async function errorMessage(response: Response): Promise<string> {
  const payload = await response.json().catch(() => null) as
    | { detail?: string | { message?: string }; error?: string }
    | null;
  if (typeof payload?.detail === "string") return payload.detail;
  if (payload?.detail && typeof payload.detail === "object") {
    return payload.detail.message ?? "Analysis request failed";
  }
  return payload?.error ?? response.statusText ?? "Analysis request failed";
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<T>;
}

export async function createAnalysisRun(body: unknown, idempotencyKey: string) {
  return json<CreateAnalysisRunResponse>(await fetch("/api/analysis/runs", {
    method: "POST",
    headers: { "content-type": "application/json", "idempotency-key": idempotencyKey },
    body: JSON.stringify(body),
  }));
}

export async function listAnalysisRuns(workspaceId: string) {
  const query = new URLSearchParams({ workspace_id: workspaceId, limit: "50" });
  return json<{ items: AnalysisRun[]; next_cursor: string | null }>(
    await fetch(`/api/analysis/runs?${query}`, { cache: "no-store" }),
  );
}

export async function getAnalysisRun(runId: string) {
  return json<AnalysisRun>(await fetch(`/api/analysis/runs/${runId}`, { cache: "no-store" }));
}

export async function getAnalysisPlan(runId: string) {
  return json<AnalysisPlanResponse>(
    await fetch(`/api/analysis/runs/${runId}/plan`, { cache: "no-store" }),
  );
}

export async function controlAnalysisRun(
  run: AnalysisRun,
  operation: "pause" | "resume" | "cancel",
) {
  return json<{ changed: boolean; run: AnalysisRun }>(
    await fetch(`/api/analysis/runs/${run.run_id}/${operation}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ expected_version: run.version }),
    }),
  );
}

export async function resumeAnalysisRunAsNew(runId: string) {
  return json<CreateAnalysisRunResponse>(
    await fetch(`/api/analysis/runs/${runId}/resume-as-new`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "idempotency-key": crypto.randomUUID(),
      },
      body: "{}",
    }),
  );
}

export async function decideAnalysisPlan(args: {
  runId: string;
  planId: string;
  revision: number;
  planHash: string;
  inputSignature: string;
  guards: WorkbookVersionGuard[];
  decision: "approve" | "reject";
  reason?: "wrong_dataset" | "wrong_operation" | "wrong_target" | "too_destructive" | "other";
  comment?: string;
}) {
  return json<AnalysisPlanResponse>(
    await fetch(`/api/analysis/runs/${args.runId}/${args.decision}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        approval_type: "plan",
        plan_id: args.planId,
        plan_revision: args.revision,
        plan_hash: args.planHash,
        input_signature: args.inputSignature,
        workbook_guards: args.guards,
        decision_id: crypto.randomUUID(),
        comment: args.comment,
        reason: args.reason,
      }),
    }),
  );
}

export async function streamAnalysisEvents(args: {
  runId: string;
  after: number;
  signal: AbortSignal;
  onEvent: (event: AnalysisRunEvent) => void;
}) {
  const response = await fetch(
    `/api/analysis/runs/${args.runId}/events?after=${args.after}`,
    { signal: args.signal, cache: "no-store" },
  );
  if (!response.ok || !response.body) throw new Error(await errorMessage(response));
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const consume = (frame: string) => {
    let data = "";
    for (const line of frame.split("\n")) {
      if (line.startsWith("data:")) data += line.slice(5).trimStart();
    }
    if (!data) return;
    args.onEvent(JSON.parse(data) as AnalysisRunEvent);
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      consume(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
}

export async function uploadWorkbookSnapshot(args: {
  workspaceId: string;
  artifactId: string;
  artifactName: string;
  workbookId: string;
  worksheetId: string;
  range: string;
  hash: string;
  revision: number;
  snapshot: unknown;
}) {
  const form = new FormData();
  form.set("workspace_id", args.workspaceId);
  form.set("artifact_id", args.artifactId);
  form.set("artifact_type", "spreadsheet");
  form.set("artifact_name", args.artifactName);
  form.set("workbook_id", args.workbookId);
  form.set("worksheet_id", args.worksheetId);
  form.set("snapshot_range", args.range);
  form.set("snapshot_hash", args.hash);
  form.set("client_revision", String(args.revision));
  form.set(
    "file",
    new File([JSON.stringify(args.snapshot)], "workbook-range.json", {
      type: "application/json",
    }),
  );
  return json<{ version_id: string }>(await fetch("/api/analysis/artifacts", {
    method: "POST",
    headers: { "idempotency-key": `snapshot-${args.hash}` },
    body: form,
  }));
}
