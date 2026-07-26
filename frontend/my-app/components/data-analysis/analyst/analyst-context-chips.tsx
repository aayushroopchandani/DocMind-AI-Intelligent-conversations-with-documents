"use client";

import {
  FileSpreadsheet,
  FileText,
  Grid3x3,
  Hash,
  SquareDashedMousePointer,
  TextQuote,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { usePdfAnalystContext } from "@/lib/data-analysis/use-pdf-analyst-context";
import { activeArtifact } from "@/lib/data-analysis/workspace-state";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * Compact chips describing what the analyst is "looking at".
 *
 * For spreadsheets that is the workbook, worksheet and cell selection
 * (streamed from Univer's events). For PDFs it is the document, the current
 * page out of the total, and the user's text selection when one exists (read
 * through the app-level PDF controller).
 */
export function AnalystContextChips() {
  const { state } = useWorkspace();
  const artifact = activeArtifact(state);
  const pdfContext = usePdfAnalystContext();

  if (!artifact) {
    return (
      <p className="text-xs text-muted-foreground">
        Open a spreadsheet or PDF to give the analyst context.
      </p>
    );
  }

  if (artifact.type === "pdf") {
    const pageCount = pdfContext?.pageCount ?? artifact.pdf?.pageCount ?? null;
    const selectedText = pdfContext?.selectedText;
    // A document that failed to open has no page to cite, so the stale page
    // count from metadata must not imply the analyst can read it.
    const isReadable =
      artifact.pdf?.loadingStatus !== "missing" &&
      artifact.pdf?.loadingStatus !== "error";
    const pageNumber = isReadable
      ? (pdfContext?.pageNumber ?? artifact.pdf?.lastViewedPage)
      : null;

    return (
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-muted-foreground">Using:</span>
        <Badge variant="outline" className="max-w-40 gap-1">
          <FileText className="text-[color:var(--accent-cyan)]" />
          <span className="truncate">{artifact.name}</span>
        </Badge>
        {pageNumber ? (
          <Badge variant="outline" className="gap-1 tabular-nums">
            <Hash />
            Page {pageNumber}
            {pageCount ? ` of ${pageCount}` : null}
          </Badge>
        ) : null}
        {!isReadable ? (
          <Badge variant="outline" className="gap-1 text-muted-foreground">
            Unavailable
          </Badge>
        ) : null}
        {selectedText ? (
          <Badge variant="outline" className="max-w-44 gap-1">
            <TextQuote />
            <span className="truncate">
              “{collapseWhitespace(selectedText)}”
            </span>
          </Badge>
        ) : null}
      </div>
    );
  }

  if (artifact.type !== "spreadsheet") {
    return (
      <p className="text-xs text-muted-foreground">
        Open a spreadsheet or PDF to give the analyst context.
      </p>
    );
  }

  const { worksheetName, selectedRange } = state.analystContext;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-muted-foreground">Using:</span>
      <Badge variant="outline" className="max-w-40 gap-1">
        <FileSpreadsheet className="text-[color:var(--accent-cyan)]" />
        <span className="truncate">{artifact.name}</span>
      </Badge>
      {worksheetName ? (
        <Badge variant="outline" className="gap-1">
          <Grid3x3 />
          {worksheetName}
        </Badge>
      ) : null}
      {selectedRange ? (
        <Badge variant="outline" className="gap-1 tabular-nums">
          <SquareDashedMousePointer />
          {selectedRange}
        </Badge>
      ) : null}
    </div>
  );
}

/** Keeps a multi-line PDF selection to one readable line inside the chip. */
function collapseWhitespace(text: string): string {
  const single = text.replace(/\s+/g, " ").trim();
  return single.length > 48 ? `${single.slice(0, 48)}…` : single;
}
