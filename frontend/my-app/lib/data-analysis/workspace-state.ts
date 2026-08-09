import {
  DEFAULT_PROJECT_NAME,
  SPREADSHEET_NAME_PREFIX,
} from "@/lib/data-analysis/constants";
import type { PdfArtifactMeta } from "@/lib/data-analysis/pdf/pdf-types";
import type {
  AnalystContext,
  AnalystMode,
  AnalysisPrivacyMode,
  ArtifactMeta,
  PersistedWorkspaceState,
  SaveStatus,
  WorkspaceState,
} from "@/lib/data-analysis/types";

/**
 * Pure reducer for the data-analysis workspace. All Univer and EmbedPDF
 * objects stay outside this state — the reducer only manages serializable
 * metadata.
 */

export type WorkspaceAction =
  | { type: "HYDRATE"; payload: PersistedWorkspaceState | null }
  | { type: "ADD_ARTIFACT"; artifact: ArtifactMeta; open: boolean }
  | { type: "ADD_ARTIFACTS"; artifacts: ArtifactMeta[]; openFirst: boolean }
  | { type: "PATCH_PDF_META"; id: string; patch: Partial<PdfArtifactMeta> }
  | { type: "OPEN_ARTIFACT"; id: string }
  | { type: "ACTIVATE_TAB"; id: string }
  | { type: "CLOSE_TAB"; id: string }
  | { type: "RENAME_ARTIFACT"; id: string; name: string }
  | { type: "DELETE_ARTIFACT"; id: string }
  | { type: "SET_DIRTY"; id: string; isDirty: boolean }
  | { type: "BUMP_WORKBOOK_REVISION"; id: string }
  | { type: "SET_SAVE_STATUS"; status: SaveStatus }
  | { type: "SET_PROJECT_NAME"; name: string }
  | { type: "SET_ANALYSIS_CHAT_ID"; chatId: string }
  | { type: "SET_ANALYST_MODE"; mode: AnalystMode }
  | { type: "SET_ANALYSIS_PRIVACY_MODE"; mode: AnalysisPrivacyMode }
  | { type: "SET_ANALYST_CONTEXT"; context: Partial<AnalystContext> }
  | { type: "SET_UNIVER_READY"; ready: boolean };

export function createInitialWorkspaceState(): WorkspaceState {
  return {
    hydrated: false,
    univerReady: false,
    project: {
      id: "local-project",
      name: DEFAULT_PROJECT_NAME,
      updatedAt: Date.now(),
    },
    artifacts: [],
    openTabIds: [],
    activeTabId: null,
    spreadsheetCounter: 0,
    saveStatus: "draft",
    analystMode: "ask",
    analysisPrivacyMode: "standard",
    analystContext: { worksheetName: null, selectedRange: null },
  };
}

export function nextSpreadsheetName(state: WorkspaceState): string {
  return `${SPREADSHEET_NAME_PREFIX} ${state.spreadsheetCounter + 1}`;
}

export function findArtifact(
  state: WorkspaceState,
  id: string | null,
): ArtifactMeta | undefined {
  if (!id) return undefined;
  return state.artifacts.find((artifact) => artifact.id === id);
}

/** The artifact rendered in the centre workspace right now. */
export function activeArtifact(
  state: WorkspaceState,
): ArtifactMeta | undefined {
  return findArtifact(state, state.activeTabId);
}

/** Open tabs of one artifact type, in tab order. */
export function openArtifactsOfType(
  state: WorkspaceState,
  type: ArtifactMeta["type"],
): ArtifactMeta[] {
  return state.openTabIds
    .map((id) => findArtifact(state, id))
    .filter(
      (artifact): artifact is ArtifactMeta => artifact?.type === type,
    );
}

/** Every display name in use — upload dedupes new file names against this. */
export function artifactNames(state: WorkspaceState): Set<string> {
  return new Set(state.artifacts.map((artifact) => artifact.name));
}

function touchProject(state: WorkspaceState): WorkspaceState {
  return {
    ...state,
    project: { ...state.project, updatedAt: Date.now() },
  };
}

export function workspaceReducer(
  state: WorkspaceState,
  action: WorkspaceAction,
): WorkspaceState {
  switch (action.type) {
    case "HYDRATE": {
      if (!action.payload) return { ...state, hydrated: true };
      const { project, artifacts, openTabIds, activeTabId, spreadsheetCounter } =
        action.payload;
      const validIds = new Set(artifacts.map((artifact) => artifact.id));
      const tabs = openTabIds.filter((id) => validIds.has(id));
      return {
        ...state,
        hydrated: true,
        project,
        // Snapshots were flushed before the last unload; nothing is dirty.
        // A PDF's loading status describes the current session only, so it
        // restarts at `loading` — the host re-reads each blob from IndexedDB
        // and reports `missing` if the bytes are gone.
        artifacts: artifacts.map((artifact) => ({
          ...artifact,
          isDirty: false,
          ...(artifact.pdf
            ? {
                pdf: {
                  ...artifact.pdf,
                  loadingStatus: "loading" as const,
                  errorMessage: undefined,
                },
              }
            : null),
        })),
        openTabIds: tabs,
        activeTabId:
          activeTabId && tabs.includes(activeTabId)
            ? activeTabId
            : (tabs[0] ?? null),
        spreadsheetCounter,
        analysisPrivacyMode: (
          action.payload.analysisPrivacyMode === "schema_only" ||
          action.payload.analysisPrivacyMode === "local_only"
            ? action.payload.analysisPrivacyMode
            : "standard"
        ),
        saveStatus: artifacts.length > 0 ? "saved" : "draft",
      };
    }

    case "ADD_ARTIFACT": {
      const next = touchProject({
        ...state,
        artifacts: [...state.artifacts, action.artifact],
        spreadsheetCounter:
          action.artifact.type === "spreadsheet"
            ? state.spreadsheetCounter + 1
            : state.spreadsheetCounter,
      });
      if (!action.open) return next;
      return {
        ...next,
        openTabIds: [...next.openTabIds, action.artifact.id],
        activeTabId: action.artifact.id,
      };
    }

    case "ADD_ARTIFACTS": {
      if (action.artifacts.length === 0) return state;
      const spreadsheetsAdded = action.artifacts.filter(
        (artifact) => artifact.type === "spreadsheet",
      ).length;
      const next = touchProject({
        ...state,
        artifacts: [...state.artifacts, ...action.artifacts],
        spreadsheetCounter: state.spreadsheetCounter + spreadsheetsAdded,
      });
      if (!action.openFirst) return next;
      // Only the first artifact opens as a tab; the rest stay in the
      // explorer so a multi-file upload never floods the tab strip.
      const first = action.artifacts[0];
      return {
        ...next,
        openTabIds: [...next.openTabIds, first.id],
        activeTabId: first.id,
      };
    }

    case "PATCH_PDF_META": {
      const artifact = findArtifact(state, action.id);
      if (!artifact?.pdf) return state;
      const pdf = { ...artifact.pdf, ...action.patch };
      // Cheap identity guard: viewer events fire often and must not spam
      // renders (or the debounced metadata write) with unchanged values.
      const unchanged = (
        Object.keys(action.patch) as Array<keyof PdfArtifactMeta>
      ).every((key) => artifact.pdf?.[key] === pdf[key]);
      if (unchanged) return state;
      return {
        ...state,
        artifacts: state.artifacts.map((item) =>
          item.id === action.id ? { ...item, pdf } : item,
        ),
      };
    }

    case "OPEN_ARTIFACT": {
      if (!findArtifact(state, action.id)) return state;
      const openTabIds = state.openTabIds.includes(action.id)
        ? state.openTabIds
        : [...state.openTabIds, action.id];
      return { ...state, openTabIds, activeTabId: action.id };
    }

    case "ACTIVATE_TAB": {
      if (!state.openTabIds.includes(action.id)) return state;
      return { ...state, activeTabId: action.id };
    }

    case "CLOSE_TAB": {
      const index = state.openTabIds.indexOf(action.id);
      if (index === -1) return state;
      const openTabIds = state.openTabIds.filter((id) => id !== action.id);
      let activeTabId = state.activeTabId;
      if (state.activeTabId === action.id) {
        // Prefer the tab that slid into the closed tab's slot, else the previous one.
        activeTabId = openTabIds[index] ?? openTabIds[index - 1] ?? null;
      }
      return { ...state, openTabIds, activeTabId };
    }

    case "RENAME_ARTIFACT": {
      const name = action.name.trim();
      if (!name) return state;
      return touchProject({
        ...state,
        artifacts: state.artifacts.map((artifact) =>
          artifact.id === action.id
            ? { ...artifact, name, updatedAt: Date.now() }
            : artifact,
        ),
      });
    }

    case "DELETE_ARTIFACT": {
      const index = state.openTabIds.indexOf(action.id);
      const openTabIds = state.openTabIds.filter((id) => id !== action.id);
      let activeTabId = state.activeTabId;
      if (state.activeTabId === action.id) {
        activeTabId = openTabIds[index] ?? openTabIds[index - 1] ?? null;
      }
      return touchProject({
        ...state,
        artifacts: state.artifacts.filter(
          (artifact) => artifact.id !== action.id,
        ),
        openTabIds,
        activeTabId,
      });
    }

    case "SET_DIRTY": {
      const artifact = findArtifact(state, action.id);
      if (!artifact || artifact.isDirty === action.isDirty) return state;
      return {
        ...state,
        artifacts: state.artifacts.map((item) =>
          item.id === action.id
            ? { ...item, isDirty: action.isDirty, updatedAt: Date.now() }
            : item,
        ),
      };
    }

    case "BUMP_WORKBOOK_REVISION": {
      const artifact = findArtifact(state, action.id);
      if (!artifact || artifact.type !== "spreadsheet") return state;
      return {
        ...state,
        artifacts: state.artifacts.map((item) => item.id === action.id
          ? { ...item, workbookRevision: (item.workbookRevision ?? 0) + 1, updatedAt: Date.now() }
          : item),
      };
    }

    case "SET_SAVE_STATUS": {
      if (state.saveStatus === action.status) return state;
      return { ...state, saveStatus: action.status };
    }

    case "SET_PROJECT_NAME": {
      const name = action.name.trim() || DEFAULT_PROJECT_NAME;
      if (name === state.project.name) return state;
      return {
        ...state,
        project: { ...state.project, name, updatedAt: Date.now() },
      };
    }

    case "SET_ANALYSIS_CHAT_ID": {
      if (state.project.analysisChatId === action.chatId) return state;
      return {
        ...state,
        project: { ...state.project, analysisChatId: action.chatId, updatedAt: Date.now() },
      };
    }

    case "SET_ANALYST_MODE":
      return { ...state, analystMode: action.mode };

    case "SET_ANALYSIS_PRIVACY_MODE":
      return { ...state, analysisPrivacyMode: action.mode };

    case "SET_UNIVER_READY": {
      if (state.univerReady === action.ready) return state;
      return { ...state, univerReady: action.ready };
    }

    case "SET_ANALYST_CONTEXT": {
      const context = { ...state.analystContext, ...action.context };
      if (
        context.worksheetName === state.analystContext.worksheetName &&
        context.selectedRange === state.analystContext.selectedRange
      ) {
        return state;
      }
      return { ...state, analystContext: context };
    }

    default:
      return state;
  }
}
