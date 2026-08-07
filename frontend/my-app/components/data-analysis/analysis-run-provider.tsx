"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { toast } from "sonner";
import { createChat, uploadPdfs } from "@/lib/api";
import {
  controlAnalysisRun,
  createAnalysisRun,
  decideAnalysisPlan,
  getAnalysisPlan,
  getAnalysisRun,
  listAnalysisRuns,
  resumeAnalysisRunAsNew,
  streamAnalysisEvents,
  uploadWorkbookSnapshot,
} from "@/lib/data-analysis/analysis-api";
import type {
  AnalysisPlan,
  AnalysisRun,
  AnalysisRunEvent,
  WorkbookVersionGuard,
} from "@/lib/data-analysis/analysis-types";
import { TERMINAL_RUN_STATUSES } from "@/lib/data-analysis/analysis-types";
import { loadPdfBuffer } from "@/lib/data-analysis/pdf/pdf-storage";
import type { AnalystRequestContext } from "@/lib/data-analysis/types";
import { isPdfArtifact } from "@/lib/data-analysis/types";
import { captureWorkbookContext } from "@/lib/data-analysis/workbook-snapshot";
import { activeArtifact } from "@/lib/data-analysis/workspace-state";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

interface AnalysisRunsValue {
  activeRun: AnalysisRun | null;
  activePlan: AnalysisPlan | null;
  events: AnalysisRunEvent[];
  history: AnalysisRun[];
  submitting: boolean;
  historyLoading: boolean;
  submit: (request: AnalystRequestContext) => Promise<boolean>;
  approvePlan: () => Promise<void>;
  rejectPlan: (reason?: "wrong_dataset" | "wrong_operation" | "wrong_target" | "too_destructive" | "other") => Promise<void>;
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  cancel: () => Promise<void>;
  resumeAsNew: (run?: AnalysisRun) => Promise<void>;
  openRun: (run: AnalysisRun) => Promise<void>;
  refreshHistory: () => Promise<void>;
}

const AnalysisRunsContext = createContext<AnalysisRunsValue | null>(null);

export function useAnalysisRuns(): AnalysisRunsValue {
  const value = useContext(AnalysisRunsContext);
  if (!value) throw new Error("useAnalysisRuns must be used inside AnalysisRunProvider");
  return value;
}

async function sha256(buffer: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

export function AnalysisRunProvider({ children }: { children: ReactNode }) {
  const { state, actions } = useWorkspace();
  const [activeRun, setActiveRun] = useState<AnalysisRun | null>(null);
  const [activePlan, setActivePlan] = useState<AnalysisPlan | null>(null);
  const [events, setEvents] = useState<AnalysisRunEvent[]>([]);
  const [history, setHistory] = useState<AnalysisRun[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const streamAbortRef = useRef<AbortController | null>(null);
  const streamGenerationRef = useRef(0);
  const activeRunRef = useRef<AnalysisRun | null>(null);

  useEffect(() => { activeRunRef.current = activeRun; }, [activeRun]);

  const refreshHistory = useCallback(async () => {
    if (!state.hydrated) return;
    setHistoryLoading(true);
    try {
      setHistory((await listAnalysisRuns(state.project.id)).items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Run history could not be loaded.");
    } finally {
      setHistoryLoading(false);
    }
  }, [state.hydrated, state.project.id]);

  const loadPlan = useCallback(async (runId: string) => {
    try {
      const response = await getAnalysisPlan(runId);
      setActivePlan(response.plan);
      setActiveRun(response.run);
    } catch {
      // Planning may still be committing when the event reaches the browser.
    }
  }, []);

  const beginStream = useCallback((run: AnalysisRun, replayFrom = 0) => {
    streamAbortRef.current?.abort();
    if (run.status === "paused" || TERMINAL_RUN_STATUSES.has(run.status)) return;
    const controller = new AbortController();
    streamAbortRef.current = controller;
    const generation = ++streamGenerationRef.current;
    let cursor = replayFrom;

    const consume = async () => {
      while (!controller.signal.aborted && generation === streamGenerationRef.current) {
        try {
          await streamAnalysisEvents({
            runId: run.run_id,
            after: cursor,
            signal: controller.signal,
            onEvent: (event) => {
              if (event.sequence <= cursor) return;
              cursor = event.sequence;
              setEvents((current) => current.some((item) => item.sequence === event.sequence)
                ? current
                : [...current, event].sort((a, b) => a.sequence - b.sequence));
              setActiveRun((current) => current?.run_id === event.run_id
                ? {
                    ...current,
                    status: event.status ?? current.status,
                    phase: event.phase ?? current.phase,
                    version: Math.max(current.version, event.sequence),
                    last_event_sequence: event.sequence,
                    updated_at: event.occurred_at,
                    pause_requested: event.event_type === "pause_requested" || current.pause_requested,
                  }
                : current);
              if (["plan_ready", "plan_approval_required", "plan_approved", "plan_rejected"].includes(event.event_type)) {
                void loadPlan(event.run_id);
              }
              if (event.event_type === "run_paused" || event.status && TERMINAL_RUN_STATUSES.has(event.status)) {
                controller.abort();
                void getAnalysisRun(event.run_id).then(setActiveRun);
                void refreshHistory();
              }
            },
          });
          if (controller.signal.aborted) return;
        } catch (error) {
          if (controller.signal.aborted) return;
          console.warn("Analysis event stream reconnecting", error);
        }
        await new Promise((resolve) => window.setTimeout(resolve, 900));
      }
    };
    void consume();
  }, [loadPlan, refreshHistory]);

  const openRun = useCallback(async (run: AnalysisRun) => {
    streamAbortRef.current?.abort();
    activeRunRef.current = run;
    setActiveRun(run);
    setActivePlan(null);
    setEvents([]);
    if (run.current_plan_id) void loadPlan(run.run_id);
    beginStream(run, 0);
  }, [beginStream, loadPlan]);

  useEffect(() => {
    if (!state.hydrated) return;
    void (async () => {
      await refreshHistory();
    })();
    return () => streamAbortRef.current?.abort();
  }, [refreshHistory, state.hydrated]);

  useEffect(() => {
    if (activeRun || history.length === 0) return;
    const reconnectable = history.find((run) => !TERMINAL_RUN_STATUSES.has(run.status));
    if (!reconnectable) return;
    const timer = window.setTimeout(() => void openRun(reconnectable), 0);
    return () => window.clearTimeout(timer);
  }, [activeRun, history, openRun]);

  const preparePdf = useCallback(async (artifactId: string) => {
    const artifact = state.artifacts.find((item) => item.id === artifactId);
    if (!isPdfArtifact(artifact)) throw new Error("The active PDF is unavailable.");
    let chatId = state.project.analysisChatId;
    if (!chatId) {
      chatId = (await createChat()).id;
      actions.setAnalysisChatId(chatId);
    }
    if (artifact.pdf.analysisDocumentId && artifact.pdf.analysisChatId === chatId) {
      return { chatId, documentId: artifact.pdf.analysisDocumentId };
    }
    const buffer = await loadPdfBuffer(artifact.pdf.storageKey);
    if (!buffer) throw new Error("The PDF bytes are no longer available in this browser.");
    const documentId = await sha256(buffer);
    const response = await uploadPdfs(chatId, [new File(
      [buffer],
      artifact.pdf.originalFileName,
      { type: artifact.pdf.mimeType },
    )]);
    if (!response.documents.some((document) => document.document_id === documentId)) {
      throw new Error("The backend did not register the uploaded PDF.");
    }
    actions.patchPdfMeta(artifact.id, {
      analysisDocumentId: documentId,
      analysisChatId: chatId,
    });
    return { chatId, documentId };
  }, [actions, state.artifacts, state.project.analysisChatId]);

  const submit = useCallback(async (request: AnalystRequestContext) => {
    const artifact = activeArtifact(state);
    if (!artifact || !request.activeArtifactId) {
      toast.error("Open a spreadsheet or PDF before starting an analysis run.");
      return false;
    }
    setSubmitting(true);
    try {
      let body: Record<string, unknown>;
      if (artifact.type === "spreadsheet") {
        const captured = await captureWorkbookContext({
          preferredRange: request.spreadsheet?.selectedRange ?? null,
          revision: artifact.workbookRevision ?? 0,
        });
        if (!captured.inline) {
          const uploaded = await uploadWorkbookSnapshot({
            workspaceId: state.project.id,
            artifactId: artifact.id,
            artifactName: artifact.name,
            workbookId: captured.context.workbook_id,
            worksheetId: captured.context.worksheet_id,
            range: captured.context.snapshot_range,
            hash: captured.context.snapshot_hash,
            revision: captured.context.client_revision,
            snapshot: captured.snapshot,
          });
          captured.context.snapshot_artifact_version_id = uploaded.version_id;
        }
        body = {
          request_version: "1",
          workspace_id: state.project.id,
          mode: request.mode,
          prompt: request.prompt,
          active_artifact: {
            client_artifact_id: artifact.id,
            artifact_type: "spreadsheet",
            name: artifact.name,
          },
          spreadsheet_context: captured.context,
          selected_document_ids: [],
          client_capabilities: { sse: true, workbook_engine: "univer", workbook_engine_version: "0.25.1" },
        };
      } else if (artifact.type === "pdf") {
        const pdf = await preparePdf(artifact.id);
        body = {
          request_version: "1",
          workspace_id: state.project.id,
          mode: request.mode,
          prompt: request.prompt,
          active_artifact: {
            client_artifact_id: artifact.id,
            artifact_type: "pdf",
            name: artifact.name,
          },
          pdf_context: {
            document_ids: [pdf.documentId],
            chat_id: pdf.chatId,
            active_document_id: pdf.documentId,
            current_page: request.pdf?.pageNumber ?? 1,
          },
          selected_document_ids: [pdf.documentId],
          client_capabilities: { sse: true },
        };
      } else {
        throw new Error("This artifact type is not supported by the analysis pipeline yet.");
      }

      const response = await createAnalysisRun(body, crypto.randomUUID());
      await openRun(response.run);
      await refreshHistory();
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The analysis run could not be created.");
      return false;
    } finally {
      setSubmitting(false);
    }
  }, [openRun, preparePdf, refreshHistory, state]);

  const currentWorkbookGuards = useCallback(async (): Promise<WorkbookVersionGuard[]> => {
    if (!activePlan) return [];
    const artifact = activeArtifact(state);
    const provenance = activePlan.input_datasets.flatMap((dataset) => dataset.provenance)
      .filter((item) => item.workbook_id && item.worksheet_id && item.range_a1);
    const unique = new Map<string, typeof provenance[number]>();
    provenance.forEach((item) => unique.set(`${item.workbook_id}:${item.worksheet_id}`, item));
    if (unique.size === 0) return [];
    if (artifact?.type !== "spreadsheet" || unique.size > 1) {
      throw new Error("Open the plan's workbook before approving it.");
    }
    const source = [...unique.values()][0];
    const captured = await captureWorkbookContext({
      preferredRange: source.range_a1,
      revision: artifact.workbookRevision ?? 0,
    });
    return [{
      workbook_id: captured.context.workbook_id,
      worksheet_id: captured.context.worksheet_id,
      workbook_revision: captured.context.client_revision,
      snapshot_hash: captured.context.snapshot_hash,
    }];
  }, [activePlan, state]);

  const decide = useCallback(async (
    decision: "approve" | "reject",
    reason?: "wrong_dataset" | "wrong_operation" | "wrong_target" | "too_destructive" | "other",
  ) => {
    if (!activeRun || !activePlan) return;
    try {
      const response = await decideAnalysisPlan({
        runId: activeRun.run_id,
        planId: activePlan.plan_id,
        revision: activePlan.revision,
        planHash: activePlan.plan_hash,
        inputSignature: activePlan.input_signature,
        guards: decision === "approve" ? await currentWorkbookGuards() : [],
        decision,
        reason,
      });
      setActivePlan(response.plan);
      setActiveRun(response.run);
      await refreshHistory();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The plan decision failed.");
    }
  }, [activePlan, activeRun, currentWorkbookGuards, refreshHistory]);

  const applyControl = useCallback(async (operation: "pause" | "resume" | "cancel") => {
    const run = activeRunRef.current;
    if (!run) return;
    try {
      const response = await controlAnalysisRun(run, operation);
      activeRunRef.current = response.run;
      setActiveRun(response.run);
      await refreshHistory();
      if (operation === "resume") beginStream(response.run, response.run.last_event_sequence);
      if (operation === "cancel") streamAbortRef.current?.abort();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : `Could not ${operation} the run.`);
    }
  }, [beginStream, refreshHistory]);

  const resumeAsNew = useCallback(async (run = activeRunRef.current ?? undefined) => {
    if (!run) return;
    try {
      const response = await resumeAnalysisRunAsNew(run.run_id);
      await openRun(response.run);
      await refreshHistory();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The run could not be resumed.");
    }
  }, [openRun, refreshHistory]);

  const value = useMemo<AnalysisRunsValue>(() => ({
    activeRun,
    activePlan,
    events,
    history,
    submitting,
    historyLoading,
    submit,
    approvePlan: () => decide("approve"),
    rejectPlan: (reason = "other") => decide("reject", reason),
    pause: () => applyControl("pause"),
    resume: () => applyControl("resume"),
    cancel: () => applyControl("cancel"),
    resumeAsNew,
    openRun,
    refreshHistory,
  }), [
    activePlan, activeRun, applyControl, decide, events, history, historyLoading,
    openRun, refreshHistory, resumeAsNew, submit, submitting,
  ]);

  return <AnalysisRunsContext.Provider value={value}>{children}</AnalysisRunsContext.Provider>;
}
