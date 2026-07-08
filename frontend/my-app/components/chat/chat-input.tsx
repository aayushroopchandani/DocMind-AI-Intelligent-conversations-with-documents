"use client";

import { useRef, useEffect, type KeyboardEvent } from "react";
import { ArrowUp, Square } from "lucide-react";
import { cn } from "@/lib/utils";

interface ChatInputProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  disabled?: boolean;
  placeholder?: string;
  /** True while an answer is streaming — swaps the send button for a stop button. */
  isStreaming?: boolean;
  /** Aborts the in-flight generation. */
  onStop?: () => void;
}

const MAX_TEXTAREA_HEIGHT = 160;

/**
 * Auto-growing composer. Enter sends; Shift+Enter inserts a newline.
 * Disabled until a PDF is selected (empty state handled by the parent).
 */
export function ChatInput({
  value,
  onChange,
  onSend,
  disabled = false,
  placeholder = "Ask a question about this document…",
  isStreaming = false,
  onStop,
}: ChatInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize the textarea to fit its content, up to a max height.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`;
  }, [value]);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!disabled && value.trim()) onSend();
    }
  };

  const canSend = !disabled && value.trim().length > 0;

  return (
    <div
      className={cn(
        "flex items-end gap-2 rounded-2xl border border-border bg-card p-2 transition-colors focus-within:border-foreground/30",
        disabled && "opacity-60",
      )}
    >
      <textarea
        ref={textareaRef}
        rows={1}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        aria-label="Chat message"
        className="scrollbar-thin max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed"
      />
      {isStreaming && onStop ? (
        <button
          type="button"
          onClick={onStop}
          aria-label="Stop generating"
          title="Stop generating"
          className="inline-flex size-8 shrink-0 items-center justify-center rounded-xl border border-destructive/40 bg-destructive/15 text-destructive transition-colors hover:bg-destructive/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Square className="size-3.5 fill-current" />
        </button>
      ) : (
        <button
          type="button"
          onClick={onSend}
          disabled={!canSend}
          aria-label="Send message"
          className="inline-flex size-8 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-30"
        >
          <ArrowUp className="size-4" />
        </button>
      )}
    </div>
  );
}
