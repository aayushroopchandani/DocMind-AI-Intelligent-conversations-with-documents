"use client";

import { useCallback, useRef, useState, type KeyboardEvent } from "react";
import { SendHorizonal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  notifyPendingFeature,
} from "@/lib/data-analysis/feedback";
import { AnalystContextChips } from "@/components/data-analysis/analyst/analyst-context-chips";

/**
 * Prompt composer for the AI analyst.
 *
 * The backend is not connected yet, so `onSubmit` defaults to an honest
 * "still being connected" notice and the draft is preserved. When the agent
 * service ships, pass a real submit handler — the UI needs no other change.
 */
interface AnalystComposerProps {
  onSubmit?: (prompt: string) => void;
  draft: string;
  onDraftChange: (draft: string) => void;
}

const MAX_COMPOSER_HEIGHT = 160;

export function AnalystComposer({
  onSubmit,
  draft,
  onDraftChange,
}: AnalystComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [focused, setFocused] = useState(false);

  const resize = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_COMPOSER_HEIGHT)}px`;
  }, []);

  const handleSend = useCallback(() => {
    const prompt = draft.trim();
    if (!prompt) return;
    if (onSubmit) {
      onSubmit(prompt);
      return;
    }
    // No backend yet: keep the draft so nothing the user typed is lost.
    notifyPendingFeature("analyst");
  }, [draft, onSubmit]);

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
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
          placeholder="Ask the analyst about the active spreadsheet…"
          aria-label="Message the AI analyst"
          className="max-h-40 min-h-8 flex-1 resize-none border-0 bg-transparent p-1.5 text-sm shadow-none focus-visible:ring-0 dark:bg-transparent"
        />
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                size="icon-sm"
                aria-label="Send message"
                disabled={!draft.trim()}
                onClick={handleSend}
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
        Backend connection pending — messages are not sent yet.
      </p>
    </div>
  );
}
