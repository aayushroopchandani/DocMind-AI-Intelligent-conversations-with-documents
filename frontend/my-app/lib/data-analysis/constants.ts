import type { LayoutState } from "@/lib/data-analysis/types";

/** Bump when the persisted shape changes; older data is discarded safely. */
export const STORAGE_SCHEMA_VERSION = 1;

const STORAGE_PREFIX = "docmind.data-analysis.v1";

export const WORKSPACE_STORAGE_KEY = `${STORAGE_PREFIX}.workspace`;
export const LAYOUT_STORAGE_KEY = `${STORAGE_PREFIX}.layout`;
export const WORKBOOK_STORAGE_PREFIX = `${STORAGE_PREFIX}.workbook.`;

export const DEFAULT_PROJECT_NAME = "Untitled Analysis";
export const SPREADSHEET_NAME_PREFIX = "Untitled spreadsheet";

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
};
