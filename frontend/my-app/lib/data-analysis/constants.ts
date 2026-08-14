import type { LayoutState } from "@/lib/data-analysis/types";

/** Bump when the persisted shape changes; older data is discarded safely. */
export const STORAGE_SCHEMA_VERSION = 1;

const STORAGE_PREFIX = "docmind.data-analysis.v1";

export const WORKSPACE_STORAGE_KEY = `${STORAGE_PREFIX}.workspace`;
export const LAYOUT_STORAGE_KEY = `${STORAGE_PREFIX}.layout`;
export const WORKBOOK_STORAGE_PREFIX = `${STORAGE_PREFIX}.workbook.`;

export const DEFAULT_PROJECT_NAME = "Untitled Analysis";
export const SPREADSHEET_NAME_PREFIX = "Untitled spreadsheet";

/* ------------------------------------------------------------------ */
/* PDF documents                                                       */
/* ------------------------------------------------------------------ */

/** IndexedDB database holding uploaded PDF blobs (never localStorage). */
export const PDF_DB_NAME = "docmind.data-analysis.pdfs";
export const PDF_DB_VERSION = 1;
export const PDF_DB_STORE = "blobs";

/** Bump when the stored blob record shape changes; older rows are dropped. */
export const PDF_RECORD_SCHEMA_VERSION = 1;

/** Files accepted by the upload input. */
export const PDF_ACCEPT_ATTRIBUTE = ".pdf,application/pdf";

/* ------------------------------------------------------------------ */
/* Spreadsheet import                                                  */
/* ------------------------------------------------------------------ */

/** Files accepted by the spreadsheet import input. */
export const SPREADSHEET_ACCEPT_ATTRIBUTE =
  ".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv";

/**
 * Mirrors `ANALYSIS_MAX_SPREADSHEET_BYTES` on the backend. Used for copy
 * only — the backend is the authority and rejects anything larger.
 */
export const MAX_SPREADSHEET_IMPORT_MB = 10;
export const PDF_MIME_TYPE = "application/pdf";

/** Maximum PDFs accepted in a single upload operation. */
export const MAX_PDF_UPLOAD_BATCH = 2;

/** Refuse absurdly large files before reading them into memory (200 MB). */
export const MAX_PDF_FILE_BYTES = 200 * 1024 * 1024;

/**
 * PDFium WebAssembly binary. Served from `public/pdfium/` by the
 * `predev`/`prebuild` copy script so the viewer never depends on a CDN.
 */
export const PDFIUM_WASM_URL =
  process.env.NEXT_PUBLIC_PDFIUM_WASM_URL ?? "/pdfium/pdfium.wasm";

/** PDF thumbnail sidebar (px). */
export const PDF_THUMBNAIL_WIDTH = 104;
export const PDF_SIDEBAR_WIDTH = 168;

/** Debounce for persisting a PDF's page/zoom into artifact metadata. */
export const PDF_VIEW_STATE_SAVE_DEBOUNCE_MS = 600;

/** How long a citation highlight pulses before settling. */
export const PDF_CITATION_PULSE_MS = 2200;

/** Left explorer panel (px). */
export const LEFT_PANEL_DEFAULT = 260;
export const LEFT_PANEL_MIN = 216;
export const LEFT_PANEL_MAX = 340;
export const LEFT_PANEL_COLLAPSE_AT = 150;

/** Right analyst panel (px). */
export const RIGHT_PANEL_DEFAULT = 384;
export const RIGHT_PANEL_MIN = 320;
export const RIGHT_PANEL_MAX = 480;
export const RIGHT_PANEL_COLLAPSE_AT = 220;

/** Collapsed side panels become a narrow icon rail. */
export const PANEL_RAIL_WIDTH = 48;

/** The centre workspace must never shrink below this. */
export const CENTER_MIN_WIDTH = 440;

/** Viewport below which side panels move into overlay sheets. */
export const DESKTOP_MEDIA_QUERY = "(min-width: 1024px)";

/** Debounce for flushing workbook snapshots to localStorage. */
export const SNAPSHOT_SAVE_DEBOUNCE_MS = 900;

/** Debounce for persisting workspace metadata (names, tabs, project). */
export const METADATA_SAVE_DEBOUNCE_MS = 350;

export const DEFAULT_LAYOUT: LayoutState = {
  leftSize: LEFT_PANEL_DEFAULT,
  rightSize: RIGHT_PANEL_DEFAULT,
  leftCollapsed: false,
  rightCollapsed: false,
  ribbonCollapsed: false,
};
