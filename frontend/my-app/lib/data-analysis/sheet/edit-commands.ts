import {
  getApi,
  withRange,
  withSheet,
  withWorkbook,
} from "@/lib/data-analysis/sheet/sheet-api";

/**
 * Edit menu behaviour — undo/redo, clipboard and clearing.
 *
 * Undo/redo go through the *workbook* facade rather than `univerAPI.undo()`
 * so they stay scoped to the workbook the user is looking at, matching the
 * global ⌘Z handler in the shell.
 */

export function undo(): void {
  withWorkbook((workbook) => workbook.undo());
}

export function redo(): void {
  withWorkbook((workbook) => workbook.redo());
}

/* ------------------------------------------------------------------ */
/* Clipboard                                                           */
/* ------------------------------------------------------------------ */

/**
 * Univer's clipboard runs through the async browser Clipboard API, which
 * can be refused (permissions, insecure context, no user gesture). Each of
 * these resolves to `false` in that case rather than throwing, so callers
 * can fall back to telling the user to use the native shortcut.
 */
export async function copySelection(): Promise<boolean> {
  try {
    return (await getApi()?.copy()) ?? false;
  } catch {
    return false;
  }
}

export async function pasteIntoSelection(): Promise<boolean> {
  try {
    return (await getApi()?.paste()) ?? false;
  } catch {
    return false;
  }
}

/** Copy, then blank the source cells — Univer has no single "cut" command. */
export async function cutSelection(): Promise<boolean> {
  const copied = await copySelection();
  if (!copied) return false;
  withRange((range) => range.clearContent());
  return true;
}

/* ------------------------------------------------------------------ */
/* Clearing and selection                                              */
/* ------------------------------------------------------------------ */

export function clearValues(): void {
  withRange((range) => range.clearContent());
}

export function clearAll(): void {
  withRange((range) => range.clear());
}

export function selectAll(): void {
  withSheet((sheet) => {
    sheet
      .getRange(0, 0, sheet.getMaxRows(), sheet.getMaxColumns())
      .activate();
  });
}

/** Selects the populated block only — cheaper to act on than the whole grid. */
export function selectDataRange(): void {
  withSheet((sheet) => sheet.getDataRange().activate());
}
