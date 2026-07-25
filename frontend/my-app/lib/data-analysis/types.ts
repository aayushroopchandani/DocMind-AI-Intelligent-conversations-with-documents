/**
 * Shared types for the /data-analysis workspace.
 *
 * The workspace is designed around "artifacts": files or generated outputs
 * that open as tabs in the central workspace. Only spreadsheets are
 * implemented in this milestone — the other artifact types exist so PDFs,
 * charts, reports and dashboards can be added later without restructuring.
 */

/** Every kind of artifact the workspace will eventually render. */
export type ArtifactType =
  | "spreadsheet"
  | "pdf"
  | "chart"
  | "report"
  | "dashboard";

/** Where an artifact came from. `generated` is reserved for agent output. */
export type ArtifactSource = "created" | "imported" | "generated";

export interface ArtifactMeta {
  id: string;
  name: string;
  type: ArtifactType;
  source: ArtifactSource;
  createdAt: number;
  updatedAt: number;
  /** True while edits exist that have not been flushed to localStorage. */
  isDirty: boolean;
}

export interface ProjectMeta {
  id: string;
  name: string;
  updatedAt: number;
}

/** Local draft persistence status shown in the top bar. */
export type SaveStatus = "draft" | "saving" | "saved";

export type AnalystMode = "ask" | "analyse" | "edit";

/** Live spreadsheet context mirrored into the AI analyst panel. */
export interface AnalystContext {
  worksheetName: string | null;
  selectedRange: string | null;
}

export interface WorkspaceState {
  /** False until localStorage has been read on the client. */
  hydrated: boolean;
  /** True while the Univer instance is booted and its facade is usable. */
  univerReady: boolean;
  project: ProjectMeta;
  artifacts: ArtifactMeta[];
  openTabIds: string[];
  activeTabId: string | null;
  /** Monotonic counter behind "Untitled spreadsheet N" names. */
  spreadsheetCounter: number;
  saveStatus: SaveStatus;
  analystMode: AnalystMode;
  analystContext: AnalystContext;
}

/** Subset of workspace state persisted to localStorage. */
export interface PersistedWorkspaceState {
  schemaVersion: number;
  project: ProjectMeta;
  artifacts: ArtifactMeta[];
  openTabIds: string[];
  activeTabId: string | null;
  spreadsheetCounter: number;
}

/** Panel layout, persisted separately so drags don't rewrite workbook data. */
export interface LayoutState {
  leftSize: number;
  rightSize: number;
  leftCollapsed: boolean;
  rightCollapsed: boolean;
}
