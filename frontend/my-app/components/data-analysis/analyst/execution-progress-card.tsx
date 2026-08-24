"use client";

import {
  CircleAlert,
  CircleCheck,
  Database,
  Loader,
  ShieldCheck,
  Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type {
  ExecutionProgress,
  ExecutionStage,
} from "@/lib/data-analysis/execution/execution-events";
import type { ExecutionView } from "@/lib/data-analysis/execution/execution-types";
import { formatCount } from "@/lib/data-analysis/format";
import {
  describeBytes,
  describeResultShape,
} from "@/lib/data-analysis/execution/result-preview";
import { cn } from "@/lib/utils";

/**
 * What the engine is doing, live.
 *
 * Everything here is folded from the durable event stream, so it survives a
 * reconnect: reopening a run replays from sequence zero and rebuilds the same
 * state. The record fetched afterwards fills in what the stream does not carry
 * — engine version, per-stage timings — but the card renders without it.
 */

interface Props {
  progress: ExecutionProgress;
  execution: ExecutionView | null;
}

const STAGE_LABEL: Record<ExecutionStage, string> = {
  queued: "Queued for execution",
  running: "Running",
  validating: "Validating the result",
  publishing: "Saving the result",
  completed: "Complete",
  failed: "Failed",
};

/** Stages still in motion get a spinner; the two terminal ones do not. */
const ACTIVE_STAGES: ReadonlySet<ExecutionStage> = new Set<ExecutionStage>([
  "queued",
  "running",
  "validating",
  "publishing",
]);

function StageIcon({ stage }: { stage: ExecutionStage }) {
  if (stage === "failed") return <CircleAlert className="size-3.5 text-destructive" />;
  if (stage === "completed") {
    return <CircleCheck className="size-3.5 text-[color:var(--accent-emerald)]" />;
  }
  return (
    <Loader className="size-3.5 animate-spin text-[color:var(--accent-cyan)]" />
  );
}

function StepBar({
  done,
  total,
  failed,
}: {
  done: number;
  total: number;
  failed: boolean;
}) {
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  return (
    <div
      className="mt-2 h-1 w-full overflow-hidden rounded-full bg-muted"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={total}
      aria-valuenow={done}
      aria-label={`Step ${done} of ${total}`}
    >
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-300",
          // A cyan bar under a failed run reads as progress. It stops where
          // the work stopped, in the colour of the thing that happened.
          failed ? "bg-destructive" : "bg-[color:var(--accent-cyan)]",
        )}
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}

export function ExecutionProgressCard({ progress, execution }: Props) {
  const { stage } = progress;
  if (stage === null) return null;

  const active = ACTIVE_STAGES.has(stage);
  const shape = describeResultShape(
    progress.resultRowCount,
    progress.resultColumnCount,
  );
  const size = describeBytes(progress.resultByteCount);

  return (
    <div
      className={cn(
        "rounded-lg border bg-card/60 p-3",
        stage === "failed" ? "border-destructive/40" : "border-border",
      )}
    >
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0">
          <StageIcon stage={stage} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">
            {STAGE_LABEL[stage]}
          </p>
          {progress.datasetCount !== null ? (
            <p className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground">
              <Database className="size-3 shrink-0" />
              {formatCount(progress.datasetCount)}{" "}
              {progress.datasetCount === 1 ? "dataset" : "datasets"}
              {progress.totalInputRows !== null ? (
                <> · {formatCount(progress.totalInputRows)} rows in</>
              ) : null}
            </p>
          ) : null}
        </div>
        {progress.cacheHit ? (
          <Badge variant="outline" className="shrink-0 gap-1">
            <Zap className="size-3" /> Reused
          </Badge>
        ) : null}
      </div>

      {/* A queued run has not started a step, so a counter and an empty bar
          would claim progress that has not begun. */}
      {stage !== "queued" && progress.stepCount !== null && progress.stepCount > 0 ? (
        <>
          <div className="mt-2.5 flex items-baseline justify-between text-[11px] text-muted-foreground">
            <span>
              Step {Math.min(progress.stepsCompleted, progress.stepCount)} of{" "}
              {progress.stepCount}
            </span>
            {progress.lastStep ? (
              <span className="truncate pl-2 text-right">
                {formatCount(progress.lastStep.output_rows)} rows out
              </span>
            ) : null}
          </div>
          <StepBar
            done={progress.stepsCompleted}
            total={progress.stepCount}
            failed={stage === "failed"}
          />
        </>
      ) : null}

      {progress.failure ? (
        <div className="mt-2.5 rounded-md border border-destructive/30 bg-destructive/10 p-2">
          <p className="text-[11px] font-medium text-destructive">
            {progress.failure.code.replaceAll("_", " ")}
          </p>
          {progress.failure.message ? (
            <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
              {progress.failure.message}
            </p>
          ) : null}
        </div>
      ) : null}

      {!active && stage !== "failed" && shape ? (
        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          <span className="text-foreground">{shape}</span>
          {size ? <span>{size}</span> : null}
          {execution ? <span>{execution.engine_version}</span> : null}
          {progress.contentHash ? (
            <span
              className="flex items-center gap-1"
              // The digest is what makes a replay provably identical, so it is
              // shown rather than hidden — abbreviated, with the full value on
              // hover for anyone comparing two runs.
              title={progress.contentHash}
            >
              <ShieldCheck className="size-3" />
              <span className="font-mono">{progress.contentHash.slice(0, 12)}</span>
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
