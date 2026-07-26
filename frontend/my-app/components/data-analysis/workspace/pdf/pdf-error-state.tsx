"use client";

import { FileX2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatFileSize } from "@/lib/data-analysis/pdf/pdf-validation";
import type { PdfArtifactMetaFull } from "@/lib/data-analysis/types";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * Terminal state for a PDF that cannot be shown: missing local bytes, a
 * password-protected file, or a malformed document the engine rejected.
 *
 * Rendered *inside* one tab's surface, so a broken PDF never affects other
 * open documents. The user can always close the tab or delete the artifact
 * from here. There is no "retry": every failure mode reachable here is
 * deterministic — the same bytes will fail the same way.
 */
export function PdfErrorState({
  artifact,
}: {
  artifact: PdfArtifactMetaFull;
}) {
  const { actions, ui } = useWorkspace();
  const { pdf } = artifact;
  const isMissing = pdf.loadingStatus === "missing";

  return (
    <div
      role="alert"
      className="flex h-full items-center justify-center p-6 animate-in fade-in duration-200"
    >
      <div className="flex max-w-sm flex-col items-center text-center">
        <div className="flex size-11 items-center justify-center rounded-xl border border-destructive/30 bg-destructive/10 text-destructive">
          <FileX2 className="size-5" />
        </div>
        <p className="mt-3 truncate text-sm font-medium text-foreground">
          {artifact.name}
        </p>
        <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
          {isMissing
            ? "This locally stored PDF is no longer available. Its data was cleared from this browser — upload the file again to keep reading it."
            : (pdf.errorMessage ??
              "This PDF could not be opened. It may be password-protected or damaged.")}
        </p>
        {pdf.fileSize > 0 ? (
          <p className="mt-1 text-xs text-muted-foreground/70">
            {pdf.originalFileName} · {formatFileSize(pdf.fileSize)}
          </p>
        ) : null}

        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => actions.closeTab(artifact.id)}
          >
            Close tab
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={() => ui.setDeleteTargetId(artifact.id)}
          >
            <Trash2 data-icon="inline-start" />
            Delete
          </Button>
        </div>
      </div>
    </div>
  );
}
