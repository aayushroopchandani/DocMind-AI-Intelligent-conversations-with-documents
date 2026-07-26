"use client";

import dynamic from "next/dynamic";
import { activeArtifact } from "@/lib/data-analysis/workspace-state";
import { ArtifactRenderer } from "@/components/data-analysis/workspace/artifact-renderer";
import { EmptyWorkspace } from "@/components/data-analysis/workspace/empty-workspace";
import { WorkspaceTabs } from "@/components/data-analysis/workspace/workspace-tabs";
import { UniverLoadingState } from "@/components/data-analysis/workspace/spreadsheet/univer-loading-state";
import { PdfLoadingState } from "@/components/data-analysis/workspace/pdf/pdf-loading-state";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";
import { cn } from "@/lib/utils";

// Univer is heavy and strictly browser-only: load it lazily on the client,
// and only once a spreadsheet actually opens.
const UniverHost = dynamic(
  () => import("@/components/data-analysis/workspace/spreadsheet/univer-host"),
  { ssr: false, loading: () => <UniverLoadingState /> },
);

// Same deal for EmbedPDF: the PDFium WebAssembly binary is ~4.6 MB, so a
// spreadsheet-only session must never pay for it.
const PdfHost = dynamic(
  () => import("@/components/data-analysis/workspace/pdf/pdf-host"),
  { ssr: false, loading: () => <PdfLoadingState label="Loading the PDF viewer…" /> },
);

/**
 * Centre column: artifact tab strip + the active artifact surface.
 *
 * Both document engines follow the same pattern: a host mounts while at least
 * one tab of its kind is open and is merely *hidden* (not unmounted) when a
 * different artifact type is active. Switching tabs therefore swaps which
 * host is visible instead of re-booting an engine, and each host keeps its
 * own per-document state. Closing the last tab of a kind unmounts and
 * disposes that engine entirely.
 *
 * `invisible` rather than `hidden` is deliberate: it keeps both surfaces in
 * layout so their ResizeObservers stay accurate and nothing needs re-measuring
 * when the user switches back.
 */
export function WorkspaceShell() {
  const { state } = useWorkspace();
  const active = activeArtifact(state);
  const activeIsSpreadsheet = active?.type === "spreadsheet";
  const activeIsPdf = active?.type === "pdf";

  const hasTabOfType = (type: "spreadsheet" | "pdf") =>
    state.openTabIds.some((id) =>
      state.artifacts.some(
        (artifact) => artifact.id === id && artifact.type === type,
      ),
    );

  const hasSpreadsheetTabs = hasTabOfType("spreadsheet");
  const hasPdfTabs = hasTabOfType("pdf");

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col bg-background">
      <WorkspaceTabs />
      <div className="relative min-h-0 min-w-0 flex-1">
        {hasSpreadsheetTabs ? (
          <HostSlot visible={activeIsSpreadsheet}>
            <UniverHost />
          </HostSlot>
        ) : null}

        {hasPdfTabs ? (
          <HostSlot visible={activeIsPdf}>
            <PdfHost />
          </HostSlot>
        ) : null}

        {active ? <ArtifactRenderer artifact={active} /> : <EmptyWorkspace />}
      </div>
    </div>
  );
}

function HostSlot({
  visible,
  children,
}: {
  visible: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn("absolute inset-0", !visible && "invisible pointer-events-none")}
      aria-hidden={!visible}
    >
      {children}
    </div>
  );
}
