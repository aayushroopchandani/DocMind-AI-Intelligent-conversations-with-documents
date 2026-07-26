"use client";

import { EmbedPDF } from "@embedpdf/core/react";
import { usePdfiumEngine } from "@embedpdf/engines/react";
import { TriangleAlert } from "lucide-react";
import { PDFIUM_WASM_URL } from "@/lib/data-analysis/constants";
import { isPdfArtifact } from "@/lib/data-analysis/types";
import { findArtifact } from "@/lib/data-analysis/workspace-state";
import { PdfDocumentSync } from "@/components/data-analysis/workspace/pdf/pdf-document-sync";
import { PdfErrorState } from "@/components/data-analysis/workspace/pdf/pdf-error-state";
import { PdfLoadingState } from "@/components/data-analysis/workspace/pdf/pdf-loading-state";
import { PDF_PLUGINS } from "@/components/data-analysis/workspace/pdf/pdf-plugins";
import { PdfWorkspace } from "@/components/data-analysis/workspace/pdf/pdf-workspace";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * The engine runs inside a Web Worker created from a Blob URL, so its base
 * URL is `blob:<origin>/<uuid>`. A root-relative path cannot be resolved
 * against that — `fetch("/pdfium/pdfium.wasm")` throws "Failed to parse URL"
 * in the worker — so the configured path is absolutised here.
 */
function resolveWasmUrl(url: string): string {
  if (typeof window === "undefined" || /^https?:\/\//.test(url)) return url;
  return new URL(url, window.location.origin).href;
}

/**
 * Module-level so the object identity never changes: `usePdfiumEngine`
 * re-initialises the WebAssembly engine when its config changes. Safe to
 * evaluate at import time because this module is only ever loaded in the
 * browser (`next/dynamic` with `ssr: false`).
 *
 * The wasm is served from this origin (see `scripts/copy-pdfium-wasm.mjs`)
 * rather than EmbedPDF's default jsDelivr CDN, so the viewer works offline
 * and makes no third-party request. Running in a worker keeps page
 * rasterisation off the main thread, so the workspace UI never blocks.
 */
const ENGINE_CONFIG = { wasmUrl: resolveWasmUrl(PDFIUM_WASM_URL) } as const;

/**
 * Hosts the single EmbedPDF instance for the whole workspace.
 *
 * One engine + one provider holds every open PDF, keyed by artifact id — the
 * exact counterpart to `univer-host`, which holds one Univer instance with a
 * workbook unit per spreadsheet.
 *
 * State-preservation strategy: **documents stay loaded, viewers do not.**
 * Every open PDF tab keeps a parsed document (and its page, zoom, rotation,
 * scroll offset and search results) in the engine's store for as long as its
 * tab is open, but only the active PDF's viewer is mounted in the DOM. That
 * caps DOM and canvas memory at one document regardless of how many are open,
 * while re-activating a tab restores instantly from the retained state —
 * EmbedPDF's `onLayoutReady({ isInitial: false })` exists for exactly this
 * hand-off. `lastViewedPage` and `zoomLevel` are additionally persisted to
 * artifact metadata, so the same restore survives a full browser reload.
 *
 * Loaded lazily (via `next/dynamic` in `workspace-shell`) and mounted only
 * while at least one PDF tab is open, so a spreadsheet-only session never
 * downloads the 4.6 MB PDFium binary. Unmounting disposes the engine, its
 * worker and every document with it.
 *
 * Default-exported for `next/dynamic`.
 */
export default function PdfHost() {
  const { state } = useWorkspace();
  const { engine, isLoading, error } = usePdfiumEngine(ENGINE_CONFIG);

  const active = findArtifact(state, state.activeTabId);

  if (error) {
    return <PdfEngineError message={error.message} />;
  }

  if (isLoading || !engine) {
    return <PdfLoadingState label="Starting the PDF engine…" />;
  }

  return (
    <EmbedPDF engine={engine} plugins={PDF_PLUGINS}>
      {({ pluginsReady }) =>
        pluginsReady ? (
          <>
            {/*
              Headless, and always mounted: it keeps every open PDF tab's
              document alive in the engine even while a spreadsheet is the
              active tab. Only the *viewer* below unmounts in that case.
            */}
            <PdfDocumentSync />
            {isPdfArtifact(active) ? (
              active.pdf.loadingStatus === "missing" ? (
                // The blob is gone, so no document was ever opened — show the
                // error here instead of inside a document that cannot exist.
                <PdfErrorState artifact={active} />
              ) : (
                <PdfWorkspace key={active.id} artifact={active} />
              )
            ) : null}
          </>
        ) : (
          <PdfLoadingState label="Preparing the viewer…" />
        )
      }
    </EmbedPDF>
  );
}

function PdfEngineError({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="flex h-full items-center justify-center p-6 bg-background"
    >
      <div className="flex max-w-sm flex-col items-center text-center">
        <div className="flex size-11 items-center justify-center rounded-xl border border-destructive/30 bg-destructive/10 text-destructive">
          <TriangleAlert className="size-5" />
        </div>
        <p className="mt-3 text-sm font-medium text-foreground">
          The PDF engine could not start
        </p>
        <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
          {message}
        </p>
        <p className="mt-2 text-xs text-muted-foreground/70">
          Spreadsheets in this workspace are unaffected.
        </p>
      </div>
    </div>
  );
}
