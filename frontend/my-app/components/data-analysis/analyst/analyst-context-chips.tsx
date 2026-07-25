"use client";

import { FileSpreadsheet, Grid3x3, SquareDashedMousePointer } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { activeArtifact } from "@/lib/data-analysis/workspace-state";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * Compact chips describing what the analyst is "looking at": the active
 * workbook, its worksheet and the current selection. Values stream in from
 * Univer's ActiveSheetChanged / SelectionChanged events.
 */
export function AnalystContextChips() {
  const { state } = useWorkspace();
  const artifact = activeArtifact(state);

  if (!artifact || artifact.type !== "spreadsheet") {
    return (
      <p className="text-xs text-muted-foreground">
        Open a spreadsheet to give the analyst context.
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
