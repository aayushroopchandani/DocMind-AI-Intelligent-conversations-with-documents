"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { DocumentContent } from "@embedpdf/plugin-document-manager/react";
import type { PdfArtifactMetaFull } from "@/lib/data-analysis/types";
import { PdfControllerBridge } from "@/components/data-analysis/workspace/pdf/pdf-controller-bridge";
import { PdfErrorState } from "@/components/data-analysis/workspace/pdf/pdf-error-state";
import { PdfLoadingState } from "@/components/data-analysis/workspace/pdf/pdf-loading-state";
import { PdfThumbnailSidebar } from "@/components/data-analysis/workspace/pdf/pdf-thumbnail-sidebar";
import { PdfToolbar } from "@/components/data-analysis/workspace/pdf/pdf-toolbar";
import { PdfViewerSurface } from "@/components/data-analysis/workspace/pdf/pdf-viewer-surface";

/** Fold secondary toolbar controls into the overflow menu below this width. */
const COMPACT_BREAKPOINT = 720;

/**
 * Below this the thumbnail sidebar would leave too little room for the page,
 * so it is suppressed (and its toggle hidden) regardless of preference.
 */
const SIDEBAR_MIN_WIDTH = 560;

/**
 * One PDF's complete workspace surface: toolbar, thumbnail sidebar and page
 * canvas, laid out to fill the centre column exactly like the spreadsheet
 * editor does. No oversized card wraps the document.
 */
export function PdfWorkspace({ artifact }: { artifact: PdfArtifactMetaFull }) {
  const documentId = artifact.id;
  const containerRef = useRef<HTMLDivElement>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [searchOpen, setSearchOpen] = useState(false);
  const [width, setWidth] = useState(Number.POSITIVE_INFINITY);

  /* ---------------- responsiveness ---------------- */

  // Watches the container, not the window: the toolbar and sidebar must adapt
  // when the explorer or analyst panel is dragged, at any window size.
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const isCompact = width < COMPACT_BREAKPOINT;
  const canShowSidebar = width >= SIDEBAR_MIN_WIDTH;
  // Derived, so a narrow column hides the sidebar without discarding the
  // user's preference — widening the panel brings it straight back.
  const sidebarVisible = sidebarOpen && canShowSidebar;

  /* ---------------- Cmd/Ctrl+F opens search ---------------- */

  const handleKeyDown = useCallback((event: React.KeyboardEvent) => {
    if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "f") {
      return;
    }
    // Scoped to this subtree via onKeyDown rather than a window listener, so
    // the browser's own find stays available in the analyst composer, the
    // project-name field and the spreadsheet.
    event.preventDefault();
    setSearchOpen(true);
  }, []);

  return (
    <div
      ref={containerRef}
      onKeyDown={handleKeyDown}
      aria-label={`PDF document: ${artifact.name}`}
      className="flex h-full min-h-0 min-w-0 flex-col bg-background"
    >
      <DocumentContent documentId={documentId}>
        {({ isLoading, isError, isLoaded }) => (
          <>
            {isLoading ? <PdfLoadingState /> : null}
            {isError ? <PdfErrorState artifact={artifact} /> : null}
            {isLoaded ? (
              <>
                <PdfToolbar
                  documentId={documentId}
                  documentName={artifact.name}
                  sidebarOpen={sidebarVisible}
                  onToggleSidebar={() => setSidebarOpen((open) => !open)}
                  canToggleSidebar={canShowSidebar}
                  searchOpen={searchOpen}
                  onSearchOpenChange={setSearchOpen}
                  isCompact={isCompact}
                />
                <div className="flex min-h-0 min-w-0 flex-1">
                  {sidebarVisible ? (
                    <PdfThumbnailSidebar documentId={documentId} />
                  ) : null}
                  <div className="relative min-h-0 min-w-0 flex-1">
                    <PdfViewerSurface documentId={documentId} />
                  </div>
                </div>
                <PdfControllerBridge artifact={artifact} />
              </>
            ) : null}
          </>
        )}
      </DocumentContent>
    </div>
  );
}
