"use client";

import { useEffect, useState } from "react";
import { getCurrentPdfContext } from "@/lib/data-analysis/pdf/pdf-controller";
import type { PdfAnalystContext } from "@/lib/data-analysis/pdf/pdf-types";
import { isPdfArtifact } from "@/lib/data-analysis/types";
import { activeArtifact } from "@/lib/data-analysis/workspace-state";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/** How often the analyst panel refreshes the active PDF's page/selection. */
const POLL_MS = 700;

/**
 * Live PDF context for the AI analyst panel.
 *
 * Reads through the app-level PDF controller rather than EmbedPDF hooks, so
 * the analyst panel — which lives outside the viewer's provider tree — has no
 * dependency on the rendering engine at all.
 *
 * Polls at a slow interval instead of subscribing: the current page and text
 * selection change often, and a light poll keeps the analyst panel out of the
 * viewer's render path entirely. Only runs while a PDF is the active tab.
 */
export function usePdfAnalystContext(): PdfAnalystContext | null {
  const { state } = useWorkspace();
  const active = activeArtifact(state);
  const activeId = isPdfArtifact(active) ? active.id : null;
  const [context, setContext] = useState<PdfAnalystContext | null>(null);

  useEffect(() => {
    if (!activeId) return;

    let cancelled = false;
    const read = async () => {
      const next = await getCurrentPdfContext();
      // Drop a result that arrived after the user switched tabs.
      if (cancelled || next?.artifactId !== activeId) return;
      setContext(next);
    };

    void read();
    const interval = setInterval(read, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [activeId]);

  // Derived rather than cleared in an effect: a stale context from a
  // previously active PDF is filtered out here, so switching tabs never
  // renders another document's page number.
  return context?.artifactId === activeId ? context : null;
}
