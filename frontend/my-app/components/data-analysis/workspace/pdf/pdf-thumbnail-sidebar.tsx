"use client";

import { useScroll } from "@embedpdf/plugin-scroll/react";
import { ThumbImg, ThumbnailsPane } from "@embedpdf/plugin-thumbnail/react";
import { PDF_SIDEBAR_WIDTH } from "@/lib/data-analysis/constants";
import { cn } from "@/lib/utils";

/**
 * Page thumbnails for the active PDF.
 *
 * This is the *document's* page list — distinct from the workspace file
 * explorer on the far left, which lists project files. EmbedPDF virtualizes
 * the pane (only visible thumbnails are rasterised and kept in the DOM) and
 * auto-scrolls it to follow the current page, so a 500-page PDF costs the
 * same as a 5-page one.
 */
export function PdfThumbnailSidebar({ documentId }: { documentId: string }) {
  const { provides: scroll, state } = useScroll(documentId);

  return (
    <div
      aria-label="Page thumbnails"
      style={{ width: PDF_SIDEBAR_WIDTH }}
      className="relative min-h-0 shrink-0 overflow-hidden border-r border-border bg-card/20"
    >
      <ThumbnailsPane
        documentId={documentId}
        className="scrollbar-thin"
        style={{ height: "100%", overflowY: "auto" }}
      >
        {(meta) => {
          const pageNumber = meta.pageIndex + 1;
          const isCurrent = state.currentPage === pageNumber;
          return (
            <div
              key={meta.pageIndex}
              style={{
                position: "absolute",
                top: meta.top,
                height: meta.wrapperHeight,
                width: "100%",
              }}
              className="flex flex-col items-center justify-start"
            >
              <button
                type="button"
                aria-label={`Go to page ${pageNumber}`}
                aria-current={isCurrent ? "page" : undefined}
                onClick={() => scroll?.scrollToPage({ pageNumber })}
                style={{ width: meta.width, height: meta.height }}
                className={cn(
                  "overflow-hidden rounded-md border bg-white outline-none transition-colors",
                  "focus-visible:ring-2 focus-visible:ring-ring/50",
                  isCurrent
                    ? "border-[color:var(--accent-cyan)] shadow-[0_0_0_1px_var(--accent-cyan)]"
                    : "border-border hover:border-[color:var(--accent-cyan)]/50",
                )}
              >
                <ThumbImg
                  documentId={documentId}
                  meta={meta}
                  style={{
                    width: "100%",
                    height: "100%",
                    objectFit: "contain",
                    display: "block",
                  }}
                />
              </button>
              <span
                style={{ height: meta.labelHeight }}
                className={cn(
                  "flex items-center text-[10px] tabular-nums",
                  isCurrent
                    ? "text-[color:var(--accent-cyan)]"
                    : "text-muted-foreground",
                )}
              >
                {pageNumber}
              </span>
            </div>
          );
        }}
      </ThumbnailsPane>
    </div>
  );
}
