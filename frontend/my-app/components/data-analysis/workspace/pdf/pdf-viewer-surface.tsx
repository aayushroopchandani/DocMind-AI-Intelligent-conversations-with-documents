"use client";

import { Copy } from "lucide-react";
import {
  GlobalPointerProvider,
  PagePointerProvider,
} from "@embedpdf/plugin-interaction-manager/react";
import { RenderLayer } from "@embedpdf/plugin-render/react";
import { Rotate } from "@embedpdf/plugin-rotate/react";
import { Scroller } from "@embedpdf/plugin-scroll/react";
import { SearchLayer } from "@embedpdf/plugin-search/react";
import {
  SelectionLayer,
  useSelectionCapability,
  type SelectionSelectionMenuProps,
} from "@embedpdf/plugin-selection/react";
import { TilingLayer } from "@embedpdf/plugin-tiling/react";
import { Viewport } from "@embedpdf/plugin-viewport/react";
import { MarqueeZoom, ZoomGestureWrapper } from "@embedpdf/plugin-zoom/react";
import { PdfCitationLayer } from "@/components/data-analysis/workspace/pdf/pdf-citation-layer";

/**
 * The scrollable page canvas for one PDF.
 *
 * Layer order matters and follows EmbedPDF's reference composition:
 *   Rotate → PagePointerProvider → Render (base bitmap) → Tiling (sharp
 *   tiles at the current zoom) → Search highlights → citation highlights →
 *   MarqueeZoom → Selection (topmost, owns pointer interaction).
 *
 * The viewport is `absolute inset-0` inside a `min-w-0 min-h-0` parent, which
 * is what lets EmbedPDF's own ResizeObserver re-measure correctly whenever
 * the workspace panels are dragged — no manual resize plumbing needed.
 */
export function PdfViewerSurface({ documentId }: { documentId: string }) {
  return (
    <GlobalPointerProvider
      documentId={documentId}
      style={{ position: "absolute", inset: 0 }}
    >
      <Viewport
        documentId={documentId}
        className="scrollbar-thin"
        style={{
          position: "absolute",
          inset: 0,
          overflow: "auto",
          // Matches the workspace surface rather than EmbedPDF's light grey.
          backgroundColor: "var(--background)",
        }}
      >
        <ZoomGestureWrapper documentId={documentId}>
          <Scroller
            documentId={documentId}
            renderPage={({ pageIndex }) => (
              <Rotate
                documentId={documentId}
                pageIndex={pageIndex}
                style={{
                  backgroundColor: "#ffffff",
                  boxShadow: "0 1px 8px -2px rgb(0 0 0 / 0.55)",
                }}
              >
                <PagePointerProvider
                  documentId={documentId}
                  pageIndex={pageIndex}
                >
                  <RenderLayer
                    documentId={documentId}
                    pageIndex={pageIndex}
                    scale={1}
                    style={{ pointerEvents: "none" }}
                  />
                  <TilingLayer
                    documentId={documentId}
                    pageIndex={pageIndex}
                    style={{ pointerEvents: "none" }}
                  />
                  <SearchLayer
                    documentId={documentId}
                    pageIndex={pageIndex}
                    highlightColor="#f5d90a"
                    activeHighlightColor="#ff9f1a"
                  />
                  <PdfCitationLayer
                    documentId={documentId}
                    pageIndex={pageIndex}
                  />
                  <MarqueeZoom documentId={documentId} pageIndex={pageIndex} />
                  <SelectionLayer
                    documentId={documentId}
                    pageIndex={pageIndex}
                    background="rgba(80, 190, 220, 0.45)"
                    selectionMenu={(props) => (
                      <CopySelectionMenu {...props} documentId={documentId} />
                    )}
                  />
                </PagePointerProvider>
              </Rotate>
            )}
          />
        </ZoomGestureWrapper>
      </Viewport>
    </GlobalPointerProvider>
  );
}

/**
 * Inline "Copy" affordance over selected text. Cmd/Ctrl+C works natively via
 * the text layer; this makes copying discoverable on touch too.
 */
function CopySelectionMenu({
  rect,
  menuWrapperProps,
  placement,
  documentId,
}: SelectionSelectionMenuProps & { documentId: string }) {
  const { provides: selection } = useSelectionCapability();

  return (
    <div {...menuWrapperProps}>
      <div
        style={{
          position: "absolute",
          top: placement.suggestTop ? -40 : rect.size.height + 8,
          pointerEvents: "auto",
        }}
      >
        <button
          type="button"
          onClick={() => {
            const scope = selection?.forDocument(documentId);
            scope?.copyToClipboard();
            scope?.clear();
          }}
          className="flex items-center gap-1.5 rounded-lg border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <Copy className="size-3" />
          Copy
        </button>
      </div>
    </div>
  );
}
