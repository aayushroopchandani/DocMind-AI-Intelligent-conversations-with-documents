"use client";

import { useEffect, useRef } from "react";
import { Sparkles, FileText } from "lucide-react";
import type { ChatMessage as ChatMessageType } from "@/lib/types";
import { ChatMessage, TypingIndicator } from "@/components/chat/chat-message";
import { ChatInput } from "@/components/chat/chat-input";
import { SuggestedQuestions } from "@/components/chat/suggested-questions";
import { SUGGESTED_QUESTIONS } from "@/lib/mock-chat";

interface ChatPanelProps {
  messages: ChatMessageType[];
  isResponding: boolean;
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onSelectSuggested: (question: string) => void;
  onCitationClick: (pageNumber: number) => void;
  /** True until a PDF is loaded — the composer is locked until then. */
  hasDocument: boolean;
}

/** Right-hand conversation panel: transcript, suggestions, and composer. */
export function ChatPanel({
  messages,
  isResponding,
  input,
  onInputChange,
  onSend,
  onSelectSuggested,
  onCitationClick,
  hasDocument,
}: ChatPanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to the newest message / typing indicator.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isResponding]);

  const showWelcome = hasDocument && messages.length === 0;

  return (
    <div className="flex h-full flex-col bg-card/30">
      {/* Transcript */}
      <div className="scrollbar-thin flex-1 space-y-6 overflow-y-auto p-4 sm:p-6">
        {!hasDocument ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
            <div className="inline-flex size-12 items-center justify-center rounded-2xl border border-border bg-card text-muted-foreground">
              <FileText className="size-6" />
            </div>
            <p className="text-sm font-medium text-foreground">
              No document yet
            </p>
            <p className="max-w-xs text-sm text-muted-foreground">
              Upload a PDF to start chatting. Your questions will appear here.
            </p>
          </div>
        ) : null}

        {showWelcome ? (
          <div className="rounded-2xl border border-border bg-card p-5">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium text-foreground">
              <Sparkles className="size-4" />
              Welcome to DocMind
            </div>
            <p className="text-sm leading-relaxed text-muted-foreground">
              Your document is ready. Ask anything about it, or pick one of the
              suggestions below to get started. Every answer includes citations
              you can click to jump to the source page.
            </p>
          </div>
        ) : null}

        {messages.map((message) => (
          <ChatMessage
            key={message.id}
            message={message}
            onCitationClick={onCitationClick}
          />
        ))}

        {isResponding ? <TypingIndicator /> : null}

        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div className="space-y-3 border-t border-border bg-card/60 p-4">
        {hasDocument ? (
          <SuggestedQuestions
            questions={SUGGESTED_QUESTIONS}
            onSelect={onSelectSuggested}
            disabled={isResponding}
          />
        ) : null}
        <ChatInput
          value={input}
          onChange={onInputChange}
          onSend={onSend}
          disabled={!hasDocument || isResponding}
          placeholder={
            hasDocument
              ? "Ask a question about this document…"
              : "Upload a PDF to start chatting…"
          }
        />
      </div>
    </div>
  );
}
