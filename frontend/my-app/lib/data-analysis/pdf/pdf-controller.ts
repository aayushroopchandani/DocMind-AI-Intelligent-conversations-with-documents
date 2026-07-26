import {
  clearCitation,
  setCitation,
} from "@/lib/data-analysis/pdf/pdf-citations";
import type {
  PdfAnalystContext,
  PdfCitation,
  PdfRect,
} from "@/lib/data-analysis/pdf/pdf-types";

/**
 * Application-level PDF controller.
 *
 * This is the *only* surface the rest of the app (and the future AI analyst)
 * uses to drive a PDF. No EmbedPDF type ever crosses it: the viewer registers
 * a small `PdfDocumentBinding` per open document, and everything else calls
 * these stable methods. Swapping the rendering engine would mean rewriting
 * one bridge component and nothing else.
 *
 * Deliberately module-level rather than React context so non-React callers —
 * an agent response handler, a toast action, a keyboard shortcut — can reach
 * it without being inside the provider tree. Mirrors `univer-bridge.ts`.
 */

/** What a mounted PDF document lets the app do to it. */
export interface PdfDocumentBinding {
  /** 1-based. Optionally scrolls to a rect in page coordinates. */
  goToPage: (pageNumber: number, rect?: PdfRect) => void;
  getCurrentPage: () => number;
  getPageCount: () => number;
  /** Resolves to the user's current text selection, if any. */
  getSelectedText: () => Promise<string | null>;
}

/** How the controller reaches workspace tab state. */
export interface PdfWorkspaceLink {
  /** Opens (or focuses) an artifact's tab. Returns false if unknown. */
  openArtifact: (artifactId: string) => boolean;
  getActivePdfArtifact: () => { id: string; name: string } | null;
}

const bindings = new Map<string, PdfDocumentBinding>();
let workspaceLink: PdfWorkspaceLink | null = null;

/* ------------------------------------------------------------------ */
/* Registration (called by the viewer bridge, not by feature code)      */
/* ------------------------------------------------------------------ */

export function registerPdfDocument(
  artifactId: string,
  binding: PdfDocumentBinding,
): () => void {
  bindings.set(artifactId, binding);
  return () => {
    // Guard against a stale unregister overwriting a fresh registration.
    if (bindings.get(artifactId) === binding) bindings.delete(artifactId);
  };
}

export function registerPdfWorkspaceLink(link: PdfWorkspaceLink): () => void {
  workspaceLink = link;
  return () => {
    if (workspaceLink === link) workspaceLink = null;
  };
}

/* ------------------------------------------------------------------ */
/* Public controller API                                               */
/* ------------------------------------------------------------------ */

/** Opens a PDF artifact's workspace tab (or focuses its existing one). */
export function openPdfArtifact(artifactId: string): boolean {
  return workspaceLink?.openArtifact(artifactId) ?? false;
}

/**
 * Navigates a PDF to a page. Returns false when the document is not mounted
 * — callers should `openPdfArtifact` first and retry once it is ready.
 */
export function goToPage(
  artifactId: string,
  pageNumber: number,
  rect?: PdfRect,
): boolean {
  const binding = bindings.get(artifactId);
  if (!binding) return false;
  binding.goToPage(pageNumber, rect);
  return true;
}

/**
 * Points the user at a cited region: opens the tab, scrolls to the page and
 * pulses the highlight. Safe to call before the viewer has mounted — the tab
 * opens and the highlight is applied as soon as the page renders.
 */
export function highlightCitation(citation: PdfCitation): boolean {
  openPdfArtifact(citation.artifactId);
  setCitation(citation);
  return goToPage(
    citation.artifactId,
    citation.pageNumber,
    citation.boundingBoxes?.[0],
  );
}

export function clearCitationHighlights(artifactId?: string): void {
  clearCitation(artifactId);
}

/**
 * What the analyst is looking at right now, or `null` when the active tab is
 * not a PDF. Async because the selected text has to come back from the
 * rendering engine.
 */
export async function getCurrentPdfContext(): Promise<PdfAnalystContext | null> {
  const active = workspaceLink?.getActivePdfArtifact() ?? null;
  if (!active) return null;

  const binding = bindings.get(active.id);
  if (!binding) {
    return {
      artifactId: active.id,
      artifactName: active.name,
      pageNumber: 1,
      pageCount: null,
      selectedText: null,
    };
  }

  return {
    artifactId: active.id,
    artifactName: active.name,
    pageNumber: binding.getCurrentPage(),
    pageCount: binding.getPageCount() || null,
    selectedText: await binding.getSelectedText(),
  };
}
