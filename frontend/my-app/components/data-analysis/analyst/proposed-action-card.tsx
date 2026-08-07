"use client";

import { Check, ListChecks, ShieldAlert, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import type { AnalysisPlan, AnalysisRun } from "@/lib/data-analysis/analysis-types";

interface Props {
  run: AnalysisRun | null;
  plan: AnalysisPlan | null;
  onApprove: () => Promise<void>;
  onReject: () => Promise<void>;
}

function operationLabel(value: string): string {
  return value.replaceAll("_", " ").replace(/^./, (character) => character.toUpperCase());
}

export function ProposedActionCard({ run, plan, onApprove, onReject }: Props) {
  if (!plan) {
    return (
      <div className="rounded-lg border border-dashed border-border/80 p-3">
        <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <ListChecks className="size-3.5" />
          {run ? operationLabel(run.phase) : "Proposed actions"}
        </p>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground/70">
          {run
            ? "The durable pipeline is preparing and validating an inspectable plan. No workbook data has been changed."
            : "Submit an analysis request to generate a validated plan before any workbook edit is allowed."}
        </p>
      </div>
    );
  }

  const pending = plan.approval.status === "pending";
  const totalRows = plan.steps.reduce((total, step) => total + step.estimate.rows_scanned, 0);
  const totalCells = plan.steps.reduce((total, step) => total + step.estimate.cells_written, 0);
  const target = plan.write_intents.find((intent) => intent.target)?.target;

  return (
    <div className="rounded-lg border border-border bg-card/60 p-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">{plan.intent}</p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            Plan revision {plan.revision} · {plan.diagnostics.repair_count} repair{plan.diagnostics.repair_count === 1 ? "" : "s"}
          </p>
        </div>
        <Badge variant={pending ? "outline" : "secondary"}>{operationLabel(plan.approval.status)}</Badge>
      </div>

      <div className="mt-2 flex flex-wrap gap-1">
        {plan.input_datasets.map((dataset) => (
          <Badge key={dataset.alias} variant="outline">
            {dataset.title} · {dataset.row_count.toLocaleString()} rows
          </Badge>
        ))}
      </div>
      <Separator className="my-2.5" />

      <ol className="flex flex-col gap-2 text-xs">
        {plan.steps.map((step, index) => (
          <li key={step.step_id} className="flex items-start gap-2">
            <span className="flex size-4 shrink-0 items-center justify-center rounded-full bg-muted text-[9px] text-muted-foreground">
              {index + 1}
            </span>
            <span className="min-w-0 flex-1 text-foreground">
              {operationLabel(step.kind)}
              <span className="ml-1 text-muted-foreground">via {step.executor}</span>
            </span>
          </li>
        ))}
      </ol>

      {target ? (
        <div className="mt-2.5 rounded-md bg-muted/40 p-2 text-xs text-muted-foreground">
          Target: workbook {target.workbook_id}, sheet {target.worksheet_id} · {operationLabel(target.placement_policy)}
        </div>
      ) : null}

      {plan.assumptions.length > 0 ? (
        <div className="mt-2.5">
          <p className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground">
            <ShieldAlert className="size-3" /> Assumptions
          </p>
          <ul className="mt-1 space-y-0.5 text-[11px] text-muted-foreground/80">
            {plan.assumptions.map((assumption) => <li key={assumption}>• {assumption}</li>)}
          </ul>
        </div>
      ) : null}

      <div className="mt-2.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <span>{totalRows.toLocaleString()} rows scanned</span>
        <span>{totalCells.toLocaleString()} cells written</span>
        <span>${plan.token_usage.estimated_cost_usd.toFixed(4)} estimated</span>
      </div>

      {pending ? (
        <div className="mt-3 flex items-center gap-1.5">
          <Button size="xs" onClick={() => void onApprove()}>
            <Check data-icon="inline-start" /> Approve plan
          </Button>
          <Button size="xs" variant="ghost" onClick={() => void onReject()}>
            <X data-icon="inline-start" /> Reject
          </Button>
        </div>
      ) : null}
    </div>
  );
}
