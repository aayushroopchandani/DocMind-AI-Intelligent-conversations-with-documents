"use client";

import { useCallback, useRef, useState, type KeyboardEvent } from "react";
import { SendHorizonal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { AnalystRequestContext } from "@/lib/data-analysis/types";
import { usePdfAnalystContext } from "@/lib/data-analysis/use-pdf-analyst-context";
import { activeArtifact } from "@/lib/data-analysis/workspace-state";
import { AnalystContextChips } from "@/components/data-analysis/analyst/analyst-context-chips";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * Prompt composer for the AI analyst.
 *
 * It captures the lightweight UI context; the durable run provider resolves
 * the live workbook snapshot or PDF ingestion identity before submission.
 */
interface AnalystComposerProps {
  onSubmit: (request: AnalystRequestContext) => Promise<boolean>;
  pending?: boolean;
  draft: string;
  onDraftChange: (draft: string) => void;
}

const MAX_COMPOSER_HEIGHT = 160;

const PLACEHOLDERS = {
  spreadsheet: "Ask the analyst about the active spreadsheet…",
  pdf: "Ask the analyst about the active document…",
  none: "Ask the analyst…",
} as const;

export function AnalystComposer({
  onSubmit,
  draft,
  onDraftChange,
  pending = false,
}: AnalystComposerProps) {
  const { state } = useWorkspace();
  const pdfContext = usePdfAnalystContext();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [focused, setFocused] = useState(false);

  const artifact = activeArtifact(state);
  const surface =
    artifact?.type === "pdf" || artifact?.type === "spreadsheet"
      ? artifact.type
      : "none";

  const resize = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_COMPOSER_HEIGHT)}px`;
  }, []);

  const handleSend = useCallback(async () => {
    const prompt = draft.trim();
    if (!prompt) return;

    const sent = await onSubmit({
        mode: state.analystMode,
        prompt,
        activeArtifactId: artifact?.id ?? null,
        activeArtifactType: artifact?.type ?? null,
        spreadsheet:
          artifact?.type === "spreadsheet" ? state.analystContext : null,
        pdf: artifact?.type === "pdf" ? pdfContext : null,
      });
    if (sent) onDraftChange("");
  }, [draft, onSubmit, onDraftChange, state.analystMode, state.analystContext, artifact, pdfContext]);

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  };

  return (
    <div className="shrink-0 border-t border-border p-3">
      <div className="pb-2">
        <AnalystContextChips />
      </div>
      <div
        className={
          "flex items-end gap-1.5 rounded-xl border bg-card/60 p-1.5 transition-colors " +
          (focused
            ? "border-[color:var(--accent-cyan)]/50"
            : "border-border")
        }
      >
        <Textarea
          ref={textareaRef}
          value={draft}
          rows={1}
          onChange={(event) => {
            onDraftChange(event.target.value);
            resize();
          }}
          onKeyDown={handleKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder={PLACEHOLDERS[surface]}
          aria-label="Message the AI analyst"
          className="max-h-40 min-h-8 flex-1 resize-none border-0 bg-transparent p-1.5 text-sm shadow-none focus-visible:ring-0 dark:bg-transparent"
        />
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                size="icon-sm"
                aria-label="Send message"
                disabled={!draft.trim() || pending}
                onClick={() => void handleSend()}
                className="shrink-0"
              >
                <SendHorizonal />
              </Button>
            }
          />
          <TooltipContent>Send — Enter</TooltipContent>
        </Tooltip>
      </div>
      <p className="pt-1.5 text-[11px] text-muted-foreground/60">
        {pending ? "Creating a durable analysis run…" : "Plans are validated before any workbook change."}
      </p>
    </div>
  );
}
