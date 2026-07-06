"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { Loader2, FileWarning } from "lucide-react";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

// Configure the pdf.js worker for Next.js. `new URL(..., import.meta.url)`
// lets the bundler emit the worker as a static asset (works with Turbopack).
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface PdfViewerProps {
  fileUrl: string;
  pageNumber: number;
  scale: number;
  fitWidth: boolean;
  onLoadSuccess: (numPages: number) => void;
}

function CenteredState({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full min-h-64 flex-col items-center justify-center gap-3 p-8 text-center text-sm text-muted-foreground">
      {children}
    </div>
  );
}

/**
 * React-PDF canvas for the locally-selected document. Supports paged
 * navigation, zoom (scale) and fit-to-width, with loading + error states.
 */
export function PdfViewer({
  fileUrl,
  pageNumber,
  scale,
  fitWidth,
  onLoadSuccess,
}: PdfViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState<number>(0);
  const [hasError, setHasError] = useState(false);

  // Track the container width so "fit width" can size the page canvas.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      // Account for horizontal padding around the page.
      setContainerWidth(Math.max(0, width - 32));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Stable options object avoids react-pdf reloading the document each render.
  const documentOptions = useMemo(
    () => ({
      cMapUrl: "https://unpkg.com/pdfjs-dist@" + pdfjs.version + "/cmaps/",
      cMapPacked: true,
    }),
    [],
  );

  return (
    <div
      ref={containerRef}
      className="scrollbar-thin h-full overflow-auto bg-background/40 p-4"
    >
      <Document
        file={fileUrl}
        options={documentOptions}
        onLoadSuccess={({ numPages }) => {
          setHasError(false);
          onLoadSuccess(numPages);
        }}
        onLoadError={() => setHasError(true)}
        loading={
          <CenteredState>
            <Loader2 className="size-6 animate-spin" />
            Loading document…
          </CenteredState>
        }
        error={
          <CenteredState>
            <FileWarning className="size-6 text-destructive" />
            We couldn’t open this PDF. Try uploading it again.
          </CenteredState>
        }
        className="flex justify-center"
      >
        {!hasError ? (
          <Page
            pageNumber={pageNumber}
            width={fitWidth && containerWidth ? containerWidth : undefined}
            scale={fitWidth ? undefined : scale}
            renderAnnotationLayer
            renderTextLayer
            className="overflow-hidden rounded-lg border border-border shadow-xl shadow-black/30 [&_canvas]:rounded-lg"
            loading={
              <CenteredState>
                <Loader2 className="size-6 animate-spin" />
                Rendering page…
              </CenteredState>
            }
          />
        ) : null}
      </Document>
    </div>
  );
}
