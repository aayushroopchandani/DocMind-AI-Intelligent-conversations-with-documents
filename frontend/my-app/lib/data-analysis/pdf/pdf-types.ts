/**
 * PDF-specific types for the /data-analysis workspace.
 *
 * Everything here is serializable. Runtime objects (File, Blob, ArrayBuffer,
 * EmbedPDF capabilities) never enter React state or localStorage — blobs live
 * in IndexedDB and the engine owns the buffer once a document is opened.
 */

/** Lifecycle of a PDF artifact inside this browser. */
export type PdfLoadingStatus =
  | "adding"
  | "loading"
  | "ready"
  | "missing"
  | "error";

/**
 * Zoom level as persisted. Numbers are literal scale factors; the string
 * values mirror EmbedPDF's `ZoomMode` without importing the viewer bundle
 * into code paths that only touch metadata.
 */
export type PdfZoomLevel = number | "automatic" | "fit-page" | "fit-width";

/** Serializable PDF facts attached to an artifact whose `type` is `"pdf"`. */
export interface PdfArtifactMeta {
  /** IndexedDB key for the stored blob (equal to the artifact id). */
  storageKey: string;
  mimeType: string;
  fileSize: number;
  /** Name exactly as the user's file system reported it. */
  originalFileName: string;
  /** 1-based; restored on reopen. */
  lastViewedPage: number;
  zoomLevel: PdfZoomLevel;
  /** Known only after the engine has parsed the document. */
  pageCount?: number;
  /** Reset to `loading` on hydration — it describes this session only. */
  loadingStatus: PdfLoadingStatus;
  /** Human-readable reason shown by the error state. */
  errorMessage?: string;
}

/** A rectangle in PDF page coordinates (origin at the page's top-left). */
export interface PdfRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * A region of a PDF the AI analyst wants to point the user at.
 *
 * Nothing produces citations yet — the analyst backend is not connected. This
 * exists so `highlightCitation()` has a stable shape to accept once it is.
 */
export interface PdfCitation {
  artifactId: string;
  /** 1-based page number. */
  pageNumber: number;
  /** Page-coordinate boxes to highlight; empty means "just go to the page". */
  boundingBoxes?: PdfRect[];
  /** Verbatim text the citation refers to, when the agent provides it. */
  selectedText?: string;
  /** Short label for UI affordances, e.g. "Table 3 · p. 12". */
  sourceLabel?: string;
}

/** What the analyst panel knows about the PDF the user is looking at. */
export interface PdfAnalystContext {
  artifactId: string;
  artifactName: string;
  pageNumber: number;
  pageCount: number | null;
  selectedText: string | null;
}

/** One rejected file from a multi-file selection, with the reason. */
export interface RejectedPdfFile {
  name: string;
  reason: string;
}

export interface PdfValidationResult {
  accepted: File[];
  rejected: RejectedPdfFile[];
  /** True when files were dropped because the per-upload cap was reached. */
  truncated: boolean;
}
