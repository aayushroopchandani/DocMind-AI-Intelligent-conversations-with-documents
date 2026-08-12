import type { FUniver } from "@univerjs/core/facade";

/**
 * Mutable, non-serializable bridge between React workspace state and the
 * live Univer instance.
 *
 * There is exactly one Univer application per page, so the bridge is a
 * module-level singleton (never React state, never persisted): the Univer
 * host assigns `api` when the engine boots and clears it on unmount, while
 * provider actions (undo, rename, duplicate, …) read it imperatively.
 * Reactive consumers subscribe to `univerReady` in the workspace reducer
 * instead of touching this object.
 */
export interface UniverBridge {
  /** Facade API — set by the Univer host once the instance boots. */
  api: FUniver | null;
  /** Root element hosting Univer, used to scope keyboard shortcuts. */
  containerEl: HTMLElement | null;
  /** Workbook unit ids currently loaded into the instance. */
  loadedUnitIds: Set<string>;
  /** Artifacts being deleted — skip the "save on unload" step for these. */
  pendingDeleteIds: Set<string>;
  /**
   * Workbooks that owe a new worksheet.
   *
   * "New blank spreadsheet" can be clicked while the workbook's unit is not
   * loaded — its tab was closed, or Univer is still booting after a tab
   * switch. The request is queued here and the host drains it once the unit
   * is live, so the click is never silently dropped.
   */
  pendingSheetInserts: Set<string>;
  /** Per-artifact debounce timers for snapshot persistence. */
  saveTimers: Map<string, ReturnType<typeof setTimeout>>;
}

const bridge: UniverBridge = {
  api: null,
  containerEl: null,
  loadedUnitIds: new Set(),
  pendingDeleteIds: new Set(),
  pendingSheetInserts: new Set(),
  saveTimers: new Map(),
};

export function getUniverBridge(): UniverBridge {
  return bridge;
}
