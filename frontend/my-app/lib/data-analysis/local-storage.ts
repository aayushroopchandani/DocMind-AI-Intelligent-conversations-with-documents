import type { IWorkbookData } from "@univerjs/core";
import {
  LAYOUT_STORAGE_KEY,
  STORAGE_SCHEMA_VERSION,
  WORKBOOK_STORAGE_PREFIX,
  WORKSPACE_STORAGE_KEY,
} from "@/lib/data-analysis/constants";
import type {
  LayoutState,
  PersistedWorkspaceState,
} from "@/lib/data-analysis/types";

/**
 * Browser-only draft persistence for the data-analysis workspace.
 *
 * Everything here is defensive: malformed or outdated payloads are treated
 * as absent instead of crashing the page, and quota errors surface as a
 * boolean so callers can warn the user without breaking editing.
 */

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

function readJson<T>(key: string): T | null {
  if (!isBrowser()) return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function writeJson(key: string, value: unknown): boolean {
  if (!isBrowser()) return false;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    // Quota exceeded or storage disabled — never crash the editor.
    return false;
  }
}

function removeKey(key: string): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Ignore — removal failures are harmless.
  }
}

/* ------------------------------------------------------------------ */
/* Workspace metadata                                                  */
/* ------------------------------------------------------------------ */

export function loadWorkspaceState(): PersistedWorkspaceState | null {
  const data = readJson<PersistedWorkspaceState>(WORKSPACE_STORAGE_KEY);
  if (
    !data ||
    data.schemaVersion !== STORAGE_SCHEMA_VERSION ||
    typeof data.project?.name !== "string" ||
    !Array.isArray(data.artifacts) ||
    !Array.isArray(data.openTabIds)
  ) {
    return null;
  }
  return data;
}

export function saveWorkspaceState(
  state: Omit<PersistedWorkspaceState, "schemaVersion">,
): boolean {
  return writeJson(WORKSPACE_STORAGE_KEY, {
    schemaVersion: STORAGE_SCHEMA_VERSION,
    ...state,
  });
}

/* ------------------------------------------------------------------ */
/* Panel layout                                                        */
/* ------------------------------------------------------------------ */

interface PersistedLayout extends LayoutState {
  schemaVersion: number;
}

export function loadLayoutState(): LayoutState | null {
  const data = readJson<PersistedLayout>(LAYOUT_STORAGE_KEY);
  if (
    !data ||
    data.schemaVersion !== STORAGE_SCHEMA_VERSION ||
    typeof data.leftSize !== "number" ||
    typeof data.rightSize !== "number"
  ) {
    return null;
  }
  return {
    leftSize: data.leftSize,
    rightSize: data.rightSize,
    leftCollapsed: Boolean(data.leftCollapsed),
    rightCollapsed: Boolean(data.rightCollapsed),
  };
}

export function saveLayoutState(layout: LayoutState): boolean {
  return writeJson(LAYOUT_STORAGE_KEY, {
    schemaVersion: STORAGE_SCHEMA_VERSION,
    ...layout,
  });
}

/* ------------------------------------------------------------------ */
/* Workbook snapshots                                                  */
/* ------------------------------------------------------------------ */

interface PersistedWorkbook {
  schemaVersion: number;
  savedAt: number;
  snapshot: Partial<IWorkbookData>;
}

function workbookKey(artifactId: string): string {
  return `${WORKBOOK_STORAGE_PREFIX}${artifactId}`;
}

export function loadWorkbookSnapshot(
  artifactId: string,
): Partial<IWorkbookData> | null {
  const data = readJson<PersistedWorkbook>(workbookKey(artifactId));
  if (
    !data ||
    data.schemaVersion !== STORAGE_SCHEMA_VERSION ||
    typeof data.snapshot !== "object" ||
    data.snapshot === null
  ) {
    return null;
  }
  return data.snapshot;
}

export function saveWorkbookSnapshot(
  artifactId: string,
  snapshot: Partial<IWorkbookData>,
): boolean {
  return writeJson(workbookKey(artifactId), {
    schemaVersion: STORAGE_SCHEMA_VERSION,
    savedAt: Date.now(),
    snapshot,
  } satisfies PersistedWorkbook);
}

export function deleteWorkbookSnapshot(artifactId: string): void {
  removeKey(workbookKey(artifactId));
}

/**
 * Removes workbook snapshots that no longer belong to any artifact — e.g.
 * left behind when the page unloaded between a snapshot write and the
 * metadata write. Called once per session after hydration.
 */
export function cleanupOrphanWorkbookSnapshots(validIds: string[]): void {
  if (!isBrowser()) return;
  try {
    const valid = new Set(validIds);
    const orphans: string[] = [];
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (!key?.startsWith(WORKBOOK_STORAGE_PREFIX)) continue;
      const id = key.slice(WORKBOOK_STORAGE_PREFIX.length);
      if (!valid.has(id)) orphans.push(key);
    }
    orphans.forEach((key) => window.localStorage.removeItem(key));
  } catch {
    // Best-effort cleanup; never break the page over storage access.
  }
}
