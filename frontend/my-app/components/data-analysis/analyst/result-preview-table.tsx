"use client";

import { useMemo } from "react";
import { EyeOff, Table2 } from "lucide-react";
import type { ExecutionPreviewResponse } from "@/lib/data-analysis/execution/execution-types";
import type { PlanColumn } from "@/lib/data-analysis/execution/execution-types";
import {
  buildPreviewTable,
  columnHeading,
} from "@/lib/data-analysis/execution/result-preview";
import { cn } from "@/lib/utils";

/**
 * The published result, as far as it is safe to show.
 *
 * The sample is bounded server-side at twenty rows and four hundred cells and
 * was redacted through the privacy gateway before it was stored, so this
 * renders all of it without virtualising — there is nothing here large enough
 * to need it. Wide results scroll inside their own container so the analyst
 * panel never scrolls sideways.
 */

interface Props {
  preview: ExecutionPreviewResponse;
  /** The execution's schema, when it has been read; supplies labels and types. */
  schema?: readonly PlanColumn[];
}

export function ResultPreviewTable({ preview, schema = [] }: Props) {
  // Formatting every cell is cheap at this size, but it is still pure work
  // proportional to the sample, and the sample only changes when a new result
  // is published.
  const table = useMemo(
    () => buildPreviewTable(preview.preview, schema),
    [preview.preview, schema],
  );

  if (table.columns.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border/80 p-3">
        <p className="text-xs text-muted-foreground">
          The result has no columns to show.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card/60">
      <div className="flex items-center gap-1.5 border-b border-border px-3 py-2">
        <Table2 className="size-3.5 shrink-0 text-[color:var(--accent-cyan)]" />
        <p className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
          Result
        </p>
        <p className="shrink-0 text-[11px] text-muted-foreground">
          {table.summary}
        </p>
      </div>

      <div className="max-h-72 overflow-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 z-10 bg-card">
            <tr>
              {table.columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={cn(
                    "whitespace-nowrap border-b border-border px-2.5 py-1.5 font-medium text-muted-foreground",
                    column.align === "end" ? "text-right" : "text-left",
                  )}
                >
                  <span className="inline-flex items-center gap-1">
                    {columnHeading(column)}
                    {column.redacted ? (
                      <EyeOff
                        className="size-3 shrink-0"
                        aria-label="Values withheld by the privacy mode"
                      />
                    ) : null}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, rowIndex) => (
              // The sample has no key of its own — it is an ordered slice of a
              // result, so its position is its identity.
              <tr key={rowIndex} className="even:bg-muted/20">
                {row.map((cell, cellIndex) => (
                  <td
                    key={table.columns[cellIndex].key}
                    className={cn(
                      "max-w-56 truncate px-2.5 py-1.5",
                      cell.align === "end"
                        ? "text-right tabular-nums"
                        : "text-left",
                      cell.muted ? "text-muted-foreground/60" : "text-foreground",
                    )}
                    title={cell.text}
                  >
                    {cell.text}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {table.redactedColumnCount > 0 ? (
        <p className="flex items-center gap-1.5 border-t border-border px-3 py-1.5 text-[11px] text-muted-foreground">
          <EyeOff className="size-3 shrink-0" />
          {table.redactedColumnCount}{" "}
          {table.redactedColumnCount === 1 ? "column is" : "columns are"}{" "}
          withheld by the workspace privacy mode.
        </p>
      ) : null}
    </div>
  );
}
