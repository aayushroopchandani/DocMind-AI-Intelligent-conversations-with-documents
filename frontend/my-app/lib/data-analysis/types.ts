/**
 * Shared types for the /data-analysis workspace.
 *
 * The workspace is designed around "artifacts": files or generated outputs
 * that open as tabs in the central workspace. Spreadsheets and PDFs are
 * implemented; the other artifact types exist so charts, reports and
 * dashboards can be added later without restructuring.
 */

import type {
  PdfAnalystContext,
  PdfArtifactMeta,
} from "@/lib/data-analysis/pdf/pdf-types";

/** Every kind of artifact the workspace will eventually render. */
export type ArtifactType =
  | "spreadsheet"
  | "pdf"
  | "chart"
  | "report"
  | "dashboard";

/**
 * Where an artifact came from. `uploaded` covers files the user brought in
 * from their file system; `generated` is reserved for agent output.
 */
export type ArtifactSource =
  | "created"
  | "uploaded"
  | "imported"
  | "generated";

export interface ArtifactMeta {
  id: string;
  name: string;
  type: ArtifactType;
  source: ArtifactSource;
  createdAt: number;
  updatedAt: number;
  /** True while edits exist that have not been flushed to localStorage. */
  isDirty: boolean;
  /** Monotonic client-side workbook revision used by plan guards. */
  workbookRevision?: number;
  /**
   * Type-specific serializable facts. Present exactly when `type === "pdf"`
   * — use `isPdfArtifact` rather than reading it directly.
   */
  pdf?: PdfArtifactMeta;
}

/** An artifact statically known to carry PDF metadata. */
export type PdfArtifactMetaFull = ArtifactMeta & {
  type: "pdf";
  pdf: PdfArtifactMeta;
};

/** Narrows an artifact to one backed by a stored PDF blob. */
export function isPdfArtifact(
  artifact: ArtifactMeta | undefined | null,
): artifact is PdfArtifactMetaFull {
  return Boolean(artifact && artifact.type === "pdf" && artifact.pdf);
}

export interface ProjectMeta {
  id: string;
  name: string;
  updatedAt: number;
  /** Opaque BFF token for the analysis PDF ingestion chat. */
  analysisChatId?: string;
}

/** Local draft persistence status shown in the top bar. */
export type SaveStatus = "draft" | "saving" | "saved";

export type AnalystMode = "ask" | "analyse" | "edit";
export type AnalysisPrivacyMode = "standard" | "schema_only" | "local_only";

/** Live spreadsheet context mirrored into the AI analyst panel. */
export interface AnalystContext {
  worksheetName: string | null;
  selectedRange: string | null;
}

/**
 * Context assembled by the composer before the durable run provider resolves
 * live workbook/PDF bytes into backend input references.
 */
export interface AnalystRequestContext {
  mode: AnalystMode;
  privacyMode: AnalysisPrivacyMode;
  prompt: string;
  activeArtifactId: string | null;
  activeArtifactType: ArtifactType | null;
  spreadsheet: AnalystContext | null;
  pdf: PdfAnalystContext | null;
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
  analysisPrivacyMode: AnalysisPrivacyMode;
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
  analysisPrivacyMode?: AnalysisPrivacyMode;
}

/** Panel layout, persisted separately so drags don't rewrite workbook data. */
export interface LayoutState {
  leftSize: number;
  rightSize: number;
  leftCollapsed: boolean;
  rightCollapsed: boolean;
  /** Univer's ribbon (tab strip + formatting toolbar) is folded away. */
  ribbonCollapsed: boolean;
}
