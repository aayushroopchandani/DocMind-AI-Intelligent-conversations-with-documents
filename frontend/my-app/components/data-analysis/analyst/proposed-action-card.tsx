"use client";

import { Check, Eye, ListChecks, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";

/**
 * Preview/apply surface for future agent actions.
 *
 * When the backend lands, the agent will propose edits (formulas, cleaned
 * columns, new sheets) which render here for review before touching the
 * workbook. Until then only the empty state renders — no fake actions ever
 * modify spreadsheet data.
 */
export interface ProposedAction {
  id: string;
  title: string;
  workbookName: string;
  sheetName: string;
  range?: string;
  edits: string[];
  onPreview?: () => void;
  onApply?: () => void;
  onReject?: () => void;
}

export function ProposedActionCard({ action }: { action?: ProposedAction }) {
  if (!action) {
    return (
      <div className="rounded-lg border border-dashed border-border/80 p-3">
        <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <ListChecks className="size-3.5" />
          Proposed actions
        </p>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground/70">
          When the analysis agent is connected, its proposed spreadsheet edits
          will appear here for preview before anything is applied.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card/60 p-3">
      <p className="text-sm font-medium text-foreground">{action.title}</p>
      <div className="mt-1.5 flex flex-wrap gap-1">
        <Badge variant="outline">{action.workbookName}</Badge>
        <Badge variant="outline">{action.sheetName}</Badge>
        {action.range ? (
          <Badge variant="outline" className="tabular-nums">
            {action.range}
          </Badge>
        ) : null}
      </div>
      <Separator className="my-2" />
      <ul className="flex flex-col gap-1 text-xs text-muted-foreground">
        {action.edits.map((edit, index) => (
          <li key={index} className="flex items-start gap-1.5">
            <span className="mt-1 size-1 shrink-0 rounded-full bg-[color:var(--accent-cyan)]" />
            {edit}
          </li>
        ))}
      </ul>
      <div className="mt-3 flex items-center gap-1.5">
        <Button size="xs" variant="outline" onClick={action.onPreview}>
          <Eye data-icon="inline-start" />
          Preview
        </Button>
        <Button size="xs" onClick={action.onApply}>
          <Check data-icon="inline-start" />
          Apply
        </Button>
        <Button size="xs" variant="ghost" onClick={action.onReject}>
          <X data-icon="inline-start" />
          Reject
        </Button>
      </div>
    </div>
  );
}
