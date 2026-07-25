"use client";

import { FileSpreadsheet, Import, Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { notifyPendingFeature } from "@/lib/data-analysis/feedback";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/** Centre-workspace state when no artifact tab is open. */
export function EmptyWorkspace() {
  const { actions } = useWorkspace();

  return (
    <div className="flex h-full items-center justify-center p-6 animate-in fade-in duration-300">
      <div className="flex w-full max-w-sm flex-col items-center text-center">
        <div className="flex size-12 items-center justify-center rounded-xl border border-border bg-card text-[color:var(--accent-cyan)]">
          <FileSpreadsheet className="size-6" />
        </div>
        <h2 className="mt-4 text-base font-semibold text-foreground">
          Start a new analysis workspace
        </h2>
        <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
          Create a blank spreadsheet now. File import and additional document
          types will be added in upcoming milestones.
        </p>
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          <Button size="sm" onClick={actions.createSpreadsheet}>
            <Plus data-icon="inline-start" />
            New blank spreadsheet
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => notifyPendingFeature("import")}
          >
            <Import data-icon="inline-start" />
            Import spreadsheet
            <Badge variant="secondary" className="ml-1 text-[10px]">
              Soon
            </Badge>
          </Button>
        </div>
      </div>
    </div>
  );
}
