"use client";

import { Activity, CircleCheck, CirclePause, Octagon, Play } from "lucide-react";
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

export function ActivityDetailsSheet() {
  const { ui } = useWorkspace();
  const runs = useAnalysisRuns();
  const run = runs.activeRun;
  const canPause = run && ["created", "active"].includes(run.status) && !run.pause_requested;
  const canCancel = run && !["succeeded", "failed", "cancelled", "expired"].includes(run.status);

  return (
    <Sheet open={ui.activityOpen} onOpenChange={ui.setActivityOpen}>
      <SheetContent side="bottom" className="max-h-[28rem]">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2 text-sm">
            <Activity className="size-4 text-[color:var(--accent-cyan)]" /> Agent activity
          </SheetTitle>
          <SheetDescription>
            Durable events can reconnect from sequence {run?.last_event_sequence ?? 0} without cancelling backend work.
          </SheetDescription>
        </SheetHeader>
        <div className="grid min-h-0 gap-3 px-4 pb-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
          <div className="rounded-lg border border-border p-3">
            {run ? (
              <>
                <p className="text-xs font-medium text-foreground">{run.prompt}</p>
                <p className="mt-1 text-xs capitalize text-muted-foreground">
                  {run.status} · {run.phase.replaceAll("_", " ")}
                </p>
                <p className="mt-2 font-mono text-[10px] text-muted-foreground">{run.run_id}</p>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {canPause ? (
                    <Button size="xs" variant="outline" onClick={() => void runs.pause()}>
                      <CirclePause data-icon="inline-start" /> Pause
                    </Button>
                  ) : null}
                  {run.status === "paused" ? (
                    <Button size="xs" onClick={() => void runs.resume()}>
                      <Play data-icon="inline-start" /> Resume
                    </Button>
                  ) : null}
                  {canCancel ? (
                    <Button size="xs" variant="destructive" onClick={() => void runs.cancel()}>
                      <Octagon data-icon="inline-start" /> Stop permanently
                    </Button>
                  ) : null}
                  {["cancelled", "failed", "expired"].includes(run.status) ? (
                    <Button size="xs" variant="outline" onClick={() => void runs.resumeAsNew()}>
                      <Play data-icon="inline-start" /> Resume as new
                    </Button>
                  ) : null}
                </div>
              </>
            ) : (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <CircleCheck className="size-3.5" /> No active task
              </p>
            )}
          </div>
          <ScrollArea className="h-52 rounded-lg border border-border">
            <ol className="space-y-2 p-3">
              {runs.events.length ? runs.events.map((event) => (
                <li key={event.event_id} className="flex gap-2 text-xs">
                  <span className="w-6 shrink-0 text-right font-mono text-[10px] text-muted-foreground">{event.sequence}</span>
                  <div>
                    <p className="capitalize text-foreground">{event.event_type.replaceAll("_", " ")}</p>
                    <p className="text-[10px] text-muted-foreground">{new Date(event.occurred_at).toLocaleTimeString()}</p>
                  </div>
                </li>
              )) : <li className="text-xs text-muted-foreground">No events loaded.</li>}
            </ol>
          </ScrollArea>
        </div>
      </SheetContent>
    </Sheet>
  );
}
