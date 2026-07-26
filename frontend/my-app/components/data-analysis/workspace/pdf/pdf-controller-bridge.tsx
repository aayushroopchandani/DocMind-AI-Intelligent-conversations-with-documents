"use client";

import { useEffect, useRef } from "react";
import { useScrollCapability } from "@embedpdf/plugin-scroll/react";
import { useSelectionCapability } from "@embedpdf/plugin-selection/react";
import {
  useZoom,
  useZoomCapability,
  ZoomMode,
  type ZoomLevel,
} from "@embedpdf/plugin-zoom/react";
import { PDF_VIEW_STATE_SAVE_DEBOUNCE_MS } from "@/lib/data-analysis/constants";
import {
  registerPdfDocument,
  type PdfDocumentBinding,
} from "@/lib/data-analysis/pdf/pdf-controller";
import type { PdfZoomLevel } from "@/lib/data-analysis/pdf/pdf-types";
import type { PdfArtifactMetaFull } from "@/lib/data-analysis/types";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * The single seam between EmbedPDF and the rest of DocMind. Renders nothing.
 *
 * Two jobs:
 *  1. Publish a `PdfDocumentBinding` to the app-level PDF controller, so the
 *     future AI analyst can navigate and highlight without ever importing an
 *     EmbedPDF type.
 *  2. Restore the artifact's last viewed page and zoom on load, and record
 *     changes back into artifact metadata (debounced) so reopening a tab or
 *     reloading the browser lands the user where they left off.
 *
 * IMPORTANT: every effect here depends on plugin *capabilities*
 * (`use*Capability`), never on the per-document scopes returned by `useZoom`
 * / `useScroll`. A capability is cached by the plugin and therefore stable,
 * whereas those hooks call `forDocument(id)` inline on each render and hand
 * back a fresh object — using one as a dependency re-runs the effect every
 * render, which for the zoom restore below is an infinite update loop.
 */
export function PdfControllerBridge({
  artifact,
}: {
  artifact: PdfArtifactMetaFull;
}) {
  const documentId = artifact.id;
  const { actions } = useWorkspace();
  const { provides: scrollCapability } = useScrollCapability();
  const { provides: selection } = useSelectionCapability();
  const { provides: zoomCapability } = useZoomCapability();
  // Only the reactive state is taken from `useZoom`; its `provides` is not
  // referentially stable (see the note above).
  const { state: zoomState } = useZoom(documentId);

  /* ---------------- expose the app-level binding ---------------- */

  useEffect(() => {
    if (!scrollCapability) return;
    const binding: PdfDocumentBinding = {
      // Scopes are resolved at call time, so the binding itself stays valid
      // for the whole document lifetime.
      goToPage: (pageNumber, rect) =>
        scrollCapability.forDocument(documentId).scrollToPage({
          pageNumber,
          behavior: "smooth",
          ...(rect
            ? {
                pageCoordinates: { x: rect.x, y: rect.y },
                alignX: 50,
                alignY: 35,
              }
            : null),
        }),
      getCurrentPage: () =>
        scrollCapability.forDocument(documentId).getCurrentPage(),
      getPageCount: () =>
        scrollCapability.forDocument(documentId).getTotalPages(),
      getSelectedText: async () => {
        const scope = selection?.forDocument(documentId);
        if (!scope) return null;
        try {
          const lines = await scope.getSelectedText().toPromise();
          const text = lines.join("\n").trim();
          return text.length > 0 ? text : null;
        } catch {
          // No selection, or the engine could not extract text (scanned page).
          return null;
        }
      },
    };
    return registerPdfDocument(documentId, binding);
  }, [documentId, scrollCapability, selection]);

  /* ---------------- restore the last viewed page ---------------- */

  /**
   * Captured at mount, before any scroll event can move it. This component is
   * keyed by artifact id and remounts whenever the tab is re-activated, so the
   * ref holds exactly the page the user left off on — whether they last saw
   * this PDF a moment ago in another tab or in a previous browser session.
   */
  const restoreTargetRef = useRef(artifact.pdf.lastViewedPage);
  const restoredRef = useRef(false);

  useEffect(() => {
    if (!scrollCapability) return;
    // `onLayoutReady` fires whenever a document's layout becomes navigable —
    // on first load *and* on every re-mount after a tab switch. Both need the
    // jump (the viewport's scrollTop is DOM state and does not survive an
    // unmount), so `isInitial` is deliberately ignored; `restoredRef` keeps it
    // to once per mount so a later re-layout never yanks the user back.
    return scrollCapability.onLayoutReady(({ documentId: id }) => {
      if (id !== documentId || restoredRef.current) return;
      restoredRef.current = true;
      const target = restoreTargetRef.current;
      if (target > 1) {
        scrollCapability.forDocument(id).scrollToPage({ pageNumber: target });
      }
    });
  }, [documentId, scrollCapability]);

  /* ---------------- persist page changes ---------------- */

  // Holds the newest page while the debounce is in flight, so unmounting can
  // flush it instead of dropping it.
  const pendingPageRef = useRef<number | null>(null);

  useEffect(() => {
    if (!scrollCapability) return;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const flush = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      const pageNumber = pendingPageRef.current;
      if (pageNumber === null) return;
      pendingPageRef.current = null;
      actions.patchPdfMeta(documentId, { lastViewedPage: pageNumber });
    };

    const unsubscribe = scrollCapability.onPageChange(
      ({ documentId: id, pageNumber }) => {
        if (id !== documentId) return;
        // Debounced: fast scrolling fires this for every page passed.
        pendingPageRef.current = pageNumber;
        if (timer) clearTimeout(timer);
        timer = setTimeout(flush, PDF_VIEW_STATE_SAVE_DEBOUNCE_MS);
      },
    );

    return () => {
      unsubscribe();
      // Switching tabs within the debounce window must not lose the page.
      flush();
    };
  }, [documentId, scrollCapability, actions]);

  /* ---------------- restore + persist zoom ---------------- */

  // Once per document: re-applying on later renders would fight the user.
  const zoomRestoredRef = useRef<string | null>(null);
  useEffect(() => {
    if (!zoomCapability || zoomRestoredRef.current === documentId) return;
    zoomRestoredRef.current = documentId;
    zoomCapability
      .forDocument(documentId)
      .requestZoom(toZoomLevel(artifact.pdf.zoomLevel));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId, zoomCapability]);

  useEffect(() => {
    const level = fromZoomLevel(zoomState.zoomLevel);
    const timer = setTimeout(() => {
      actions.patchPdfMeta(documentId, { zoomLevel: level });
    }, PDF_VIEW_STATE_SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [documentId, zoomState.zoomLevel, actions]);

  return null;
}

/* ------------------------------------------------------------------ */
/* Zoom level translation                                              */
/* ------------------------------------------------------------------ */

/**
 * `ZoomMode` is a TypeScript enum, so persisted metadata stores the plain
 * string equivalents and converts at this boundary. That keeps `lib/` free of
 * any EmbedPDF import and makes the stored value stable across upgrades.
 */
const ZOOM_MODES: Record<string, ZoomMode> = {
  automatic: ZoomMode.Automatic,
  "fit-page": ZoomMode.FitPage,
  "fit-width": ZoomMode.FitWidth,
};

function toZoomLevel(level: PdfZoomLevel): ZoomLevel {
  if (typeof level === "number") {
    return Number.isFinite(level) && level > 0 ? level : ZoomMode.FitWidth;
  }
  return ZOOM_MODES[level] ?? ZoomMode.FitWidth;
}

function fromZoomLevel(level: ZoomLevel): PdfZoomLevel {
  return typeof level === "number" ? level : (level as PdfZoomLevel);
}
