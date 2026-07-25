"use client";

import { History } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * Drawer for versioned agent runs. Runs are recorded only once the backend
 * executes real analyses, so this ships as a designed empty state.
 */
export function RunHistorySheet() {
  const { ui } = useWorkspace();

  return (
    <Sheet open={ui.historyOpen} onOpenChange={ui.setHistoryOpen}>
      <SheetContent side="right" className="w-80 sm:max-w-sm">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2 text-sm">
            <History className="size-4 text-[color:var(--accent-cyan)]" />
            Run history
          </SheetTitle>
          <SheetDescription>
            Each agent analysis becomes a revisitable, undoable run.
          </SheetDescription>
        </SheetHeader>
        <div className="flex flex-1 items-center justify-center px-4 pb-8">
          <div className="flex flex-col items-center text-center">
            <div className="flex size-10 items-center justify-center rounded-xl border border-dashed border-border text-muted-foreground">
              <History className="size-4" />
            </div>
            <p className="mt-3 text-sm font-medium text-foreground">
              No agent runs yet
            </p>
            <p className="mt-1 max-w-52 text-xs leading-relaxed text-muted-foreground">
              Runs will appear here once the data-analysis agent is connected.
            </p>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
