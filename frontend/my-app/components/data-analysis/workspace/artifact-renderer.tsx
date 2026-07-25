"use client";

import { FileQuestion } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { ArtifactMeta } from "@/lib/data-analysis/types";

/**
 * Discriminated renderer for workspace artifacts — the single extension
 * point for future document types.
 *
 * - `spreadsheet` returns null: the persistent Univer host (mounted once in
 *   WorkspaceShell so tab switches never re-boot the engine) shows through.
 * - `pdf` / `chart` / `report` / `dashboard` get real renderers in later
 *   milestones; until then a clean placeholder keeps the architecture
 *   honest without fake viewers or extra dependencies.
 */
export function ArtifactRenderer({ artifact }: { artifact: ArtifactMeta }) {
  switch (artifact.type) {
    case "spreadsheet":
      return null;
    case "pdf":
    case "chart":
    case "report":
    case "dashboard":
      return <UnsupportedArtifact artifact={artifact} />;
  }
}

function UnsupportedArtifact({ artifact }: { artifact: ArtifactMeta }) {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="flex max-w-xs flex-col items-center text-center">
        <div className="flex size-11 items-center justify-center rounded-xl border border-border bg-card text-muted-foreground">
          <FileQuestion className="size-5" />
        </div>
        <p className="mt-3 text-sm font-medium text-foreground">
          {artifact.name}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          <Badge variant="outline" className="mr-1 align-middle">
            {artifact.type}
          </Badge>
          viewers arrive in a later milestone.
        </p>
      </div>
    </div>
  );
}
