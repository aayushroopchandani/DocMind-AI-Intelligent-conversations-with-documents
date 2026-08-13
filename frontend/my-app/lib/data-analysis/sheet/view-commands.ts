import {
  getActiveSheet,
  getApi,
  getSelectionBounds,
  withSheet,
} from "@/lib/data-analysis/sheet/sheet-api";

/**
 * View menu behaviour. Zoom, gridlines and freezing live on the worksheet;
 * chrome visibility (Univer's own ribbon, formula bar and sheet footer) goes
 * through the facade's UI-part registry.
 */

/* ------------------------------------------------------------------ */
/* Zoom                                                                */
/* ------------------------------------------------------------------ */

/** Univer expresses zoom as a ratio, where 1 is 100%. */
export const ZOOM_STEPS = [0.5, 0.75, 0.9, 1, 1.25, 1.5, 2] as const;

export function getZoom(): number {
  try {
    return getActiveSheet()?.getZoom() ?? 1;
  } catch {
    return 1;
  }
}

export function setZoom(ratio: number): void {
  withSheet((sheet) => sheet.zoom(ratio));
}

/**
 * Moves to the next preset step. Ratios set from Univer's own zoom slider
 * are not on the list, so "the next step past the current ratio" is used
 * rather than an index lookup that would miss.
 */
export function stepZoom(direction: 1 | -1): void {
  const current = getZoom();
  const next =
    direction === 1
      ? ZOOM_STEPS.find((step) => step > current + 0.001)
      : [...ZOOM_STEPS].reverse().find((step) => step < current - 0.001);
  if (next) setZoom(next);
}

/* ------------------------------------------------------------------ */
/* Gridlines                                                           */
/* ------------------------------------------------------------------ */

export function areGridlinesVisible(): boolean {
  try {
    return !(getActiveSheet()?.hasHiddenGridLines() ?? false);
  } catch {
    return true;
  }
}

export function toggleGridlines(): void {
  const hide = areGridlinesVisible();
  withSheet((sheet) => sheet.setHiddenGridlines(hide));
}

/* ------------------------------------------------------------------ */
/* Freezing                                                            */
/* ------------------------------------------------------------------ */

export function getFrozenRows(): number {
  try {
    return getActiveSheet()?.getFrozenRows() ?? 0;
  } catch {
    return 0;
  }
}

export function getFrozenColumns(): number {
  try {
    return getActiveSheet()?.getFrozenColumns() ?? 0;
  } catch {
    return 0;
  }
}

export function freezeRows(rows: number): void {
  withSheet((sheet) => sheet.setFrozenRows(rows));
}

export function freezeColumns(columns: number): void {
  withSheet((sheet) => sheet.setFrozenColumns(columns));
}

/** Freezes everything above and to the left of the selected cell. */
export function freezeToSelection(): void {
  const bounds = getSelectionBounds();
  if (!bounds) return;
  withSheet((sheet) => {
    sheet.setFrozenRows(bounds.startRow);
    sheet.setFrozenColumns(bounds.startColumn);
  });
}

export function unfreeze(): void {
  withSheet((sheet) => sheet.cancelFreeze());
}

/* ------------------------------------------------------------------ */
/* Univer chrome                                                       */
/* ------------------------------------------------------------------ */

/**
 * UI parts the View menu can toggle, keyed by their facade enum member.
 *
 * The ribbon is deliberately absent: hiding it through Univer switches it off
 * instantly, so it folds through the workspace layout (and a CSS height
 * transition) instead.
 */
export const UI_PARTS = {
  footer: "FOOTER",
} as const;

export type UiPartKey = keyof typeof UI_PARTS;

export function isUiPartVisible(part: UiPartKey): boolean {
  const api = getApi();
  if (!api) return true;
  try {
    return api.isUIVisible(api.Enum.BuiltInUIPart[UI_PARTS[part]]);
  } catch {
    return true;
  }
}

export function toggleUiPart(part: UiPartKey): void {
  const api = getApi();
  if (!api) return;
  try {
    const key = api.Enum.BuiltInUIPart[UI_PARTS[part]];
    api.setUIVisible(key, !api.isUIVisible(key));
  } catch (error) {
    console.error("[data-analysis] Failed to toggle Univer UI part", error);
  }
}

/* ------------------------------------------------------------------ */
/* Browser full screen                                                 */
/* ------------------------------------------------------------------ */

export function isFullscreen(): boolean {
  return typeof document !== "undefined" && Boolean(document.fullscreenElement);
}

export function toggleFullscreen(): void {
  if (typeof document === "undefined") return;
  if (document.fullscreenElement) {
    void document.exitFullscreen().catch(() => undefined);
  } else {
    void document.documentElement.requestFullscreen().catch(() => undefined);
  }
}
