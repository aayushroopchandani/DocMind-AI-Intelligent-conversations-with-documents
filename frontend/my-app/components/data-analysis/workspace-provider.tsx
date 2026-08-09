"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
} from "react";
import {
  DEFAULT_LAYOUT,
  METADATA_SAVE_DEBOUNCE_MS,
  PDF_MIME_TYPE,
} from "@/lib/data-analysis/constants";
import {
  notifyPdfAdded,
  notifyPdfBatchTruncated,
  notifyPdfRejected,
  notifyPdfStoreFailed,
  notifyStorageFull,
} from "@/lib/data-analysis/feedback";
import {
  cleanupOrphanWorkbookSnapshots,
  deleteWorkbookSnapshot,
  loadLayoutState,
  loadWorkbookSnapshot,
  loadWorkspaceState,
  saveLayoutState,
  saveWorkbookSnapshot,
  saveWorkspaceState,
} from "@/lib/data-analysis/local-storage";
import {
  registerPdfWorkspaceLink,
  type PdfWorkspaceLink,
} from "@/lib/data-analysis/pdf/pdf-controller";
import {
  cleanupOrphanPdfBlobs,
  deletePdfBlob,
  savePdfBlob,
} from "@/lib/data-analysis/pdf/pdf-storage";
import type { PdfArtifactMeta } from "@/lib/data-analysis/pdf/pdf-types";
import {
  uniqueArtifactName,
  validatePdfSelection,
} from "@/lib/data-analysis/pdf/pdf-validation";
import type {
  AnalystMode,
  AnalysisPrivacyMode,
  ArtifactMeta,
  LayoutState,
  WorkspaceState,
} from "@/lib/data-analysis/types";
import { isPdfArtifact } from "@/lib/data-analysis/types";
import { getUniverBridge } from "@/lib/data-analysis/univer-bridge";
import {
  cloneWorkbookData,
  createBlankWorkbookData,
} from "@/lib/data-analysis/workbook-factory";
import {
  artifactNames,
  createInitialWorkspaceState,
  findArtifact,
  nextSpreadsheetName,
  workspaceReducer,
  type WorkspaceAction,
} from "@/lib/data-analysis/workspace-state";

function createId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `artifact-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

interface WorkspaceActions {
  createSpreadsheet: () => void;
  /**
   * Validates, stores and registers PDFs picked from disk or dropped onto
   * the workspace. Resolves once every accepted file is in IndexedDB.
   */
  addPdfFiles: (files: readonly File[]) => Promise<void>;
  /** Records a PDF's page/zoom so reopening restores where the user was. */
  patchPdfMeta: (id: string, patch: Partial<PdfArtifactMeta>) => void;
  openArtifact: (id: string) => void;
  activateTab: (id: string) => void;
  closeTab: (id: string) => void;
  renameArtifact: (id: string, name: string) => void;
  duplicateArtifact: (id: string) => void;
  deleteArtifact: (id: string) => void;
  setProjectName: (name: string) => void;
  setAnalysisChatId: (chatId: string) => void;
  setAnalystMode: (mode: AnalystMode) => void;
  setAnalysisPrivacyMode: (mode: AnalysisPrivacyMode) => void;
  undo: () => void;
  redo: () => void;
}

/** Transient UI state (overlays and dialogs) — never persisted. */
interface WorkspaceUiState {
  explorerSheetOpen: boolean;
  setExplorerSheetOpen: (open: boolean) => void;
  analystSheetOpen: boolean;
  setAnalystSheetOpen: (open: boolean) => void;
  historyOpen: boolean;
  setHistoryOpen: (open: boolean) => void;
  activityOpen: boolean;
  setActivityOpen: (open: boolean) => void;
  renameTarget: ArtifactMeta | null;
  setRenameTargetId: (id: string | null) => void;
  deleteTarget: ArtifactMeta | null;
  setDeleteTargetId: (id: string | null) => void;
}

interface WorkspaceContextValue {
  state: WorkspaceState;
  dispatch: Dispatch<WorkspaceAction>;
  layout: LayoutState;
  updateLayout: (patch: Partial<LayoutState>) => void;
  actions: WorkspaceActions;
  ui: WorkspaceUiState;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function useWorkspace(): WorkspaceContextValue {
  const context = useContext(WorkspaceContext);
  if (!context) {
    throw new Error("useWorkspace must be used inside WorkspaceProvider");
  }
  return context;
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(
    workspaceReducer,
    undefined,
    createInitialWorkspaceState,
  );

  // Lazy init reads localStorage on the client's first render. The server
  // falls back to defaults, which is safe: nothing layout-dependent renders
  // until the workspace has hydrated (the shell shows a skeleton).
  const [layout, setLayout] = useState<LayoutState>(
    () => loadLayoutState() ?? DEFAULT_LAYOUT,
  );

  const [explorerSheetOpen, setExplorerSheetOpen] = useState(false);
  const [analystSheetOpen, setAnalystSheetOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [renameTargetId, setRenameTargetId] = useState<string | null>(null);
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null);

  // Actions read the latest state through a ref so they stay referentially
  // stable. The ref is written post-render; actions only run from events.
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  /* ---------------- hydration (client only) ---------------- */

  useEffect(() => {
    const stored = loadWorkspaceState();
    dispatch({ type: "HYDRATE", payload: stored });
    const storedIds = stored?.artifacts.map((artifact) => artifact.id) ?? [];
    cleanupOrphanWorkbookSnapshots(storedIds);
    // Fire-and-forget: an unavailable IndexedDB must not block hydration.
    void cleanupOrphanPdfBlobs(storedIds);
  }, []);

  /* ---------------- debounced metadata persistence ---------------- */

  useEffect(() => {
    if (!state.hydrated) return;
    const timer = setTimeout(() => {
      saveWorkspaceState({
        project: state.project,
        artifacts: state.artifacts,
        openTabIds: state.openTabIds,
        activeTabId: state.activeTabId,
        spreadsheetCounter: state.spreadsheetCounter,
        analysisPrivacyMode: state.analysisPrivacyMode,
      });
    }, METADATA_SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [
    state.hydrated,
    state.project,
    state.artifacts,
    state.openTabIds,
    state.activeTabId,
    state.spreadsheetCounter,
    state.analysisPrivacyMode,
  ]);

  /* ---------------- debounced layout persistence ---------------- */

  const layoutMountedRef = useRef(false);
  useEffect(() => {
    if (!layoutMountedRef.current) {
      // The initial value came straight from storage — skip writing it back.
      layoutMountedRef.current = true;
      return;
    }
    const timer = setTimeout(() => saveLayoutState(layout), 300);
    return () => clearTimeout(timer);
  }, [layout]);

  const updateLayout = useCallback((patch: Partial<LayoutState>) => {
    setLayout((previous) => ({ ...previous, ...patch }));
  }, []);

  /* ---------------- workspace actions ---------------- */

  const createSpreadsheet = useCallback(() => {
    const current = stateRef.current;
    const id = createId();
    const name = nextSpreadsheetName(current);
    const now = Date.now();
    if (!saveWorkbookSnapshot(id, createBlankWorkbookData(id, name))) {
      notifyStorageFull();
    }
    const artifact: ArtifactMeta = {
      id,
      name,
      type: "spreadsheet",
      source: "created",
      createdAt: now,
      updatedAt: now,
      isDirty: false,
    };
    dispatch({ type: "ADD_ARTIFACT", artifact, open: true });
  }, []);

  /**
   * PDF upload is entirely local: validate → write the blob to IndexedDB →
   * register serializable metadata. Nothing is sent anywhere, and the raw
   * File never lands in React state.
   */
  const addPdfFiles = useCallback(async (files: readonly File[]) => {
    if (files.length === 0) return;

    const { accepted, rejected, truncated } = validatePdfSelection(files);
    notifyPdfRejected(rejected);
    if (truncated) notifyPdfBatchTruncated();
    if (accepted.length === 0) return;

    // Dedupe display names against the workspace *and* within this batch, so
    // two files called "report.pdf" both stay visible and distinguishable.
    const takenNames = artifactNames(stateRef.current);
    const artifacts: ArtifactMeta[] = [];

    for (const file of accepted) {
      const id = createId();
      try {
        await savePdfBlob(id, file);
      } catch (error) {
        notifyPdfStoreFailed(
          error instanceof Error
            ? error.message
            : "This browser refused the write.",
        );
        continue;
      }

      const name = uniqueArtifactName(file.name, takenNames);
      takenNames.add(name);
      const now = Date.now();
      artifacts.push({
        id,
        name,
        type: "pdf",
        source: "uploaded",
        createdAt: now,
        updatedAt: now,
        isDirty: false,
        pdf: {
          storageKey: id,
          mimeType: file.type || PDF_MIME_TYPE,
          fileSize: file.size,
          originalFileName: file.name,
          lastViewedPage: 1,
          zoomLevel: "fit-width",
          loadingStatus: "loading",
        },
      });
    }

    if (artifacts.length === 0) return;
    // Only the first opens a tab; the rest wait in the explorer.
    dispatch({ type: "ADD_ARTIFACTS", artifacts, openFirst: true });
    notifyPdfAdded(artifacts.length);
  }, []);

  const patchPdfMeta = useCallback(
    (id: string, patch: Partial<PdfArtifactMeta>) => {
      dispatch({ type: "PATCH_PDF_META", id, patch });
    },
    [],
  );

  const openArtifact = useCallback((id: string) => {
    dispatch({ type: "OPEN_ARTIFACT", id });
  }, []);

  const activateTab = useCallback((id: string) => {
    dispatch({ type: "ACTIVATE_TAB", id });
  }, []);

  const closeTab = useCallback((id: string) => {
    // The Univer host saves the unit's snapshot before unloading it, so
    // closing a tab never loses data and never deletes the artifact.
    dispatch({ type: "CLOSE_TAB", id });
  }, []);

  const renameArtifact = useCallback((id: string, name: string) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    dispatch({ type: "RENAME_ARTIFACT", id, name: trimmed });

    // PDFs carry no workbook snapshot: the display name is the only thing to
    // change, and the stored blob keeps its original file name for download.
    if (findArtifact(stateRef.current, id)?.type !== "spreadsheet") return;

    const bridge = getUniverBridge();
    if (bridge.api && bridge.loadedUnitIds.has(id)) {
      // Loaded workbook: rename through the facade so undo history and the
      // subsequent snapshot save stay consistent.
      bridge.api.getWorkbook(id)?.setName(trimmed);
    } else {
      // Cold artifact: patch the stored snapshot's workbook name directly.
      const snapshot = loadWorkbookSnapshot(id);
      if (snapshot) {
        saveWorkbookSnapshot(id, { ...snapshot, name: trimmed });
      }
    }
  }, []);

  const duplicateArtifact = useCallback((id: string) => {
    const current = stateRef.current;
    const source = current.artifacts.find((artifact) => artifact.id === id);
    // Duplication clones a workbook snapshot; copying PDF bytes has no user
    // value here, so the explorer only offers it for spreadsheets.
    if (!source || source.type !== "spreadsheet") return;
    const bridge = getUniverBridge();
    const liveWorkbook =
      bridge.api && bridge.loadedUnitIds.has(id)
        ? bridge.api.getWorkbook(id)
        : null;
    const sourceSnapshot = liveWorkbook
      ? liveWorkbook.save()
      : loadWorkbookSnapshot(id);
    if (!sourceSnapshot) return;

    const newId = createId();
    const newName = `${source.name} (copy)`;
    if (
      !saveWorkbookSnapshot(
        newId,
        cloneWorkbookData(sourceSnapshot, newId, newName),
      )
    ) {
      notifyStorageFull();
      return;
    }
    const now = Date.now();
    dispatch({
      type: "ADD_ARTIFACT",
      artifact: {
        id: newId,
        name: newName,
        type: source.type,
        source: source.source,
        createdAt: now,
        updatedAt: now,
        isDirty: false,
      },
      open: true,
    });
  }, []);

  const deleteArtifact = useCallback((id: string) => {
    const artifact = findArtifact(stateRef.current, id);

    if (isPdfArtifact(artifact)) {
      // Removing the tab unmounts the viewer, which closes the document in
      // the engine and releases its render resources; then the bytes go.
      dispatch({ type: "DELETE_ARTIFACT", id });
      void deletePdfBlob(artifact.pdf.storageKey);
      return;
    }

    // Flag the unit so the host disposes it without a farewell save.
    const bridge = getUniverBridge();
    bridge.pendingDeleteIds.add(id);
    const timer = bridge.saveTimers.get(id);
    if (timer) {
      clearTimeout(timer);
      bridge.saveTimers.delete(id);
    }
    dispatch({ type: "DELETE_ARTIFACT", id });
    deleteWorkbookSnapshot(id);
  }, []);

  const setProjectName = useCallback((name: string) => {
    dispatch({ type: "SET_PROJECT_NAME", name });
  }, []);

  const setAnalysisChatId = useCallback((chatId: string) => {
    dispatch({ type: "SET_ANALYSIS_CHAT_ID", chatId });
  }, []);

  const setAnalystMode = useCallback((mode: AnalystMode) => {
    dispatch({ type: "SET_ANALYST_MODE", mode });
  }, []);

  const setAnalysisPrivacyMode = useCallback((mode: AnalysisPrivacyMode) => {
    dispatch({ type: "SET_ANALYSIS_PRIVACY_MODE", mode });
  }, []);

  const undo = useCallback(() => {
    getUniverBridge().api?.getActiveWorkbook()?.undo();
  }, []);

  const redo = useCallback(() => {
    getUniverBridge().api?.getActiveWorkbook()?.redo();
  }, []);

  const actions = useMemo<WorkspaceActions>(
    () => ({
      createSpreadsheet,
      addPdfFiles,
      patchPdfMeta,
      openArtifact,
      activateTab,
      closeTab,
      renameArtifact,
      duplicateArtifact,
      deleteArtifact,
      setProjectName,
      setAnalysisChatId,
      setAnalystMode,
      setAnalysisPrivacyMode,
      undo,
      redo,
    }),
    [
      createSpreadsheet,
      addPdfFiles,
      patchPdfMeta,
      openArtifact,
      activateTab,
      closeTab,
      renameArtifact,
      duplicateArtifact,
      deleteArtifact,
      setProjectName,
      setAnalysisChatId,
      setAnalystMode,
      setAnalysisPrivacyMode,
      undo,
      redo,
    ],
  );

  /* ---------------- PDF controller link ---------------- */

  // Lets the (future) AI analyst open a cited PDF tab from outside the tree.
  // Reads live state through the ref, so the link never needs re-registering.
  useEffect(() => {
    const link: PdfWorkspaceLink = {
      openArtifact: (artifactId) => {
        if (!findArtifact(stateRef.current, artifactId)) return false;
        dispatch({ type: "OPEN_ARTIFACT", id: artifactId });
        return true;
      },
      getActivePdfArtifact: () => {
        const active = findArtifact(
          stateRef.current,
          stateRef.current.activeTabId,
        );
        return isPdfArtifact(active)
          ? { id: active.id, name: active.name }
          : null;
      },
    };
    return registerPdfWorkspaceLink(link);
  }, []);

  const renameTarget = useMemo(
    () =>
      state.artifacts.find((artifact) => artifact.id === renameTargetId) ??
      null,
    [state.artifacts, renameTargetId],
  );
  const deleteTarget = useMemo(
    () =>
      state.artifacts.find((artifact) => artifact.id === deleteTargetId) ??
      null,
    [state.artifacts, deleteTargetId],
  );

  const ui = useMemo<WorkspaceUiState>(
    () => ({
      explorerSheetOpen,
      setExplorerSheetOpen,
      analystSheetOpen,
      setAnalystSheetOpen,
      historyOpen,
      setHistoryOpen,
      activityOpen,
      setActivityOpen,
      renameTarget,
      setRenameTargetId,
      deleteTarget,
      setDeleteTargetId,
    }),
    [
      explorerSheetOpen,
      analystSheetOpen,
      historyOpen,
      activityOpen,
      renameTarget,
      deleteTarget,
    ],
  );

  const value = useMemo<WorkspaceContextValue>(
    () => ({ state, dispatch, layout, updateLayout, actions, ui }),
    [state, layout, updateLayout, actions, ui],
  );

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}
