import type { IWorkbookData } from "@univerjs/core";
import { getUniverBridge } from "@/lib/data-analysis/univer-bridge";

/**
 * Swap a loaded workbook's contents for a new snapshot, in place.
 *
 * Import adds whole sheets — cells, styles, merges, freeze state — which the
 * facade has no bulk API for. Rebuilding the unit from a merged snapshot is
 * both faster and atomic: the user never sees a half-imported grid.
 *
 * The bridge bookkeeping matters. `pendingDeleteIds` suppresses the farewell
 * save that would otherwise persist the *old* snapshot over the new one, and
 * `loadedUnitIds` is kept accurate so the host's reconcile effect does not
 * try to load a unit that is already there.
 */
export function replaceLoadedWorkbook(
  unitId: string,
  snapshot: Partial<IWorkbookData>,
): boolean {
  const bridge = getUniverBridge();
  const api = bridge.api;
  if (!api || !bridge.loadedUnitIds.has(unitId)) return false;

  const timer = bridge.saveTimers.get(unitId);
  if (timer) {
    clearTimeout(timer);
    bridge.saveTimers.delete(unitId);
  }

  try {
    bridge.pendingDeleteIds.add(unitId);
    api.disposeUnit(unitId);
    bridge.loadedUnitIds.delete(unitId);
    api.createWorkbook(snapshot, { makeCurrent: true });
    bridge.loadedUnitIds.add(unitId);
    return true;
  } catch (error) {
    console.error("[data-analysis] Failed to reload the workbook", error);
    return false;
  } finally {
    bridge.pendingDeleteIds.delete(unitId);
  }
}

/** The live snapshot of a loaded workbook, or null when it is not loaded. */
export function readWorkbookSnapshot(
  unitId: string,
): Partial<IWorkbookData> | null {
  const bridge = getUniverBridge();
  if (!bridge.api || !bridge.loadedUnitIds.has(unitId)) return null;
  try {
    return bridge.api.getWorkbook(unitId)?.save() ?? null;
  } catch {
    return null;
  }
}
