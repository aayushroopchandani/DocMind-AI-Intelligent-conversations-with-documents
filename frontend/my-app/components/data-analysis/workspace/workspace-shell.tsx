"use client";

import dynamic from "next/dynamic";
import { activeArtifact } from "@/lib/data-analysis/workspace-state";
import { ArtifactRenderer } from "@/components/data-analysis/workspace/artifact-renderer";
import { EmptyWorkspace } from "@/components/data-analysis/workspace/empty-workspace";
import { WorkspaceTabs } from "@/components/data-analysis/workspace/workspace-tabs";
import { UniverLoadingState } from "@/components/data-analysis/workspace/spreadsheet/univer-loading-state";
import { PdfLoadingState } from "@/components/data-analysis/workspace/pdf/pdf-loading-state";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

// Univer is heavy and strictly browser-only: load it lazily on the client,
// and only while a spreadsheet is the active artifact.
const UniverHost = dynamic(
  () => import("@/components/data-analysis/workspace/spreadsheet/univer-host"),
  { ssr: false, loading: () => <UniverLoadingState /> },
);

// Same deal for EmbedPDF: the PDFium WebAssembly binary is ~4.6 MB, so a
// spreadsheet-only session must never pay for it, and switching to a
// spreadsheet releases the PDF worker and document caches.
const PdfHost = dynamic(
  () => import("@/components/data-analysis/workspace/pdf/pdf-host"),
  { ssr: false, loading: () => <PdfLoadingState label="Loading the PDF viewer…" /> },
);

/**
 * Centre column: artifact tab strip + the active artifact surface.
 *
 * Only the engine for the active artifact type is mounted. Switching between
 * spreadsheets keeps the single Univer host alive, and switching between PDFs
 * keeps the single EmbedPDF host alive. Crossing from one type to the other
 * unmounts the inactive engine so its workers, canvases and document caches are
 * released. The existing persistence layers restore workbook snapshots and PDF
 * view metadata when that engine is mounted again.
 */
export function WorkspaceShell() {
  const { state } = useWorkspace();
  const active = activeArtifact(state);
  const activeIsSpreadsheet = active?.type === "spreadsheet";
  const activeIsPdf = active?.type === "pdf";

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col bg-background">
      <WorkspaceTabs />
      <div className="relative min-h-0 min-w-0 flex-1">
        {activeIsSpreadsheet ? (
          <div className="absolute inset-0">
            <UniverHost />
          </div>
        ) : null}

        {activeIsPdf ? (
          <div className="absolute inset-0">
            <PdfHost />
          </div>
        ) : null}

        {active ? <ArtifactRenderer artifact={active} /> : <EmptyWorkspace />}
      </div>
    </div>
  );
}
