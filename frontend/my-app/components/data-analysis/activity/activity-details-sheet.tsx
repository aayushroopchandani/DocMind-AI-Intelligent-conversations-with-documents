"use client";

import { Activity, CircleCheck } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/** Bottom drawer with agent activity — honest empty states only, for now. */
export function ActivityDetailsSheet() {
  const { ui } = useWorkspace();

  return (
    <Sheet open={ui.activityOpen} onOpenChange={ui.setActivityOpen}>
      <SheetContent side="bottom" className="max-h-72">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2 text-sm">
            <Activity className="size-4 text-[color:var(--accent-cyan)]" />
            Agent activity
          </SheetTitle>
          <SheetDescription>
            Live task progress and completed runs will stream here once the
            analysis agent is connected.
          </SheetDescription>
        </SheetHeader>
        <div className="grid gap-2 px-4 pb-4 sm:grid-cols-2">
          <div className="rounded-lg border border-dashed border-border p-3">
            <p className="text-xs font-medium text-foreground">Active tasks</p>
            <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
              <CircleCheck className="size-3.5" />
              No active tasks
            </p>
          </div>
          <div className="rounded-lg border border-dashed border-border p-3">
            <p className="text-xs font-medium text-foreground">Completed runs</p>
            <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
              <CircleCheck className="size-3.5" />
              No completed agent runs
            </p>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
