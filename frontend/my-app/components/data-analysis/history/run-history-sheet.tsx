"use client";

import { History, LoaderCircle, Play } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useAnalysisRuns } from "@/components/data-analysis/analysis-run-provider";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

export function RunHistorySheet() {
  const { ui } = useWorkspace();
  const runs = useAnalysisRuns();

  return (
    <Sheet
      open={ui.historyOpen}
      onOpenChange={(open) => {
        ui.setHistoryOpen(open);
        if (open) void runs.refreshHistory();
      }}
    >
      <SheetContent side="right" className="w-[24rem] sm:max-w-md">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2 text-sm">
            <History className="size-4 text-[color:var(--accent-cyan)]" /> Run history
          </SheetTitle>
          <SheetDescription>
            Persisted prompts, plans, approvals, warnings and terminal outcomes.
          </SheetDescription>
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1 px-4 pb-4">
          {runs.historyLoading && runs.history.length === 0 ? (
            <div className="flex h-40 items-center justify-center text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" />
            </div>
          ) : runs.history.length ? (
            <div className="space-y-2">
              {runs.history.map((run) => (
                <div key={run.run_id} className="rounded-lg border border-border bg-card/40 p-3">
                  <button
                    type="button"
                    className="w-full text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                    onClick={() => {
                      void runs.openRun(run);
                      ui.setHistoryOpen(false);
                    }}
                  >
                    <div className="flex items-start gap-2">
                      <p className="line-clamp-2 min-w-0 flex-1 text-xs font-medium text-foreground">{run.prompt}</p>
                      <Badge variant="outline" className="capitalize">{run.status}</Badge>
                    </div>
                    <p className="mt-1 text-[11px] capitalize text-muted-foreground">
                      {run.mode} · {run.phase.replaceAll("_", " ")} · plan r{run.current_plan_revision ?? "—"}
                    </p>
                    <p className="mt-1 text-[10px] text-muted-foreground/70">
                      {new Date(run.created_at).toLocaleString()}
                    </p>
                    {run.warnings_summary.length || run.errors_summary.length ? (
                      <p className="mt-1 text-[10px] text-muted-foreground">
                        {run.warnings_summary.length} warning(s) · {run.errors_summary.length} error(s)
                      </p>
                    ) : null}
                    {run.parent_run_id ? (
                      <p className="mt-1 truncate font-mono text-[9px] text-muted-foreground/70">
                        resumed from {run.parent_run_id}
                      </p>
                    ) : null}
                  </button>
                  {run.status === "paused" ? (
                    <Button
                      size="xs"
                      className="mt-2"
                      onClick={async () => {
                        await runs.openRun(run);
                        await runs.resume();
                        ui.setHistoryOpen(false);
                      }}
                    >
                      <Play data-icon="inline-start" /> Resume
                    </Button>
                  ) : null}
                  {["cancelled", "failed", "expired"].includes(run.status) ? (
                    <Button
                      size="xs"
                      variant="outline"
                      className="mt-2"
                      onClick={async () => {
                        await runs.resumeAsNew(run);
                        ui.setHistoryOpen(false);
                      }}
                    >
                      <Play data-icon="inline-start" /> Resume as new run
                    </Button>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="flex h-52 flex-col items-center justify-center text-center">
              <History className="size-5 text-muted-foreground" />
              <p className="mt-2 text-sm font-medium text-foreground">No agent runs yet</p>
              <p className="mt-1 text-xs text-muted-foreground">Your first durable analysis will appear here.</p>
            </div>
          )}
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
