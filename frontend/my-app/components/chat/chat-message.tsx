"use client";

import { Sparkles, User } from "lucide-react";
import type { ChatMessage as ChatMessageType } from "@/lib/types";
import { CitationCard } from "@/components/chat/citation-card";
import { cn } from "@/lib/utils";

interface ChatMessageProps {
  message: ChatMessageType;
  onCitationClick: (pageNumber: number) => void;
}

/** Renders a single user or assistant message, with citations for answers. */
export function ChatMessage({ message, onCitationClick }: ChatMessageProps) {
  const isUser = message.role === "user";

  return (
    <div
      className={cn(
        "flex w-full gap-3",
        isUser ? "flex-row-reverse" : "flex-row",
      )}
    >
      <div
        className={cn(
          "flex size-8 shrink-0 items-center justify-center rounded-full border border-border",
          isUser ? "bg-primary text-primary-foreground" : "bg-card text-foreground",
        )}
      >
        {isUser ? <User className="size-4" /> : <Sparkles className="size-4" />}
      </div>

      <div className={cn("flex min-w-0 max-w-[85%] flex-col gap-2", isUser && "items-end")}>
        <div
          className={cn(
            "rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
            isUser
              ? "rounded-br-sm bg-primary text-primary-foreground"
              : "rounded-bl-sm border border-border bg-card text-foreground",
          )}
        >
          {message.content}
        </div>

        {message.citations && message.citations.length > 0 ? (
          <div className="w-full space-y-1.5">
            <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Sources
            </span>
            {message.citations.map((citation, i) => (
              <CitationCard
                key={`${citation.pageNumber}-${i}`}
                citation={citation}
                onNavigate={onCitationClick}
              />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** Animated three-dot indicator shown while the assistant is "thinking". */
export function TypingIndicator() {
  return (
    <div className="flex w-full gap-3">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full border border-border bg-card text-foreground">
        <Sparkles className="size-4" />
      </div>
      <div className="flex items-center gap-1.5 rounded-2xl rounded-bl-sm border border-border bg-card px-4 py-3.5">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            className="size-1.5 animate-bounce rounded-full bg-muted-foreground"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </div>
    </div>
  );
}
