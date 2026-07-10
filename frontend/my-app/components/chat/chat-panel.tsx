"use client";

import { useEffect, useRef } from "react";
import { Sparkles, FileText } from "lucide-react";
import type { ChatMessage as ChatMessageType, Citation } from "@/lib/types";
import { ChatMessage } from "@/components/chat/chat-message";
import { ChatInput } from "@/components/chat/chat-input";
import { SuggestedQuestions } from "@/components/chat/suggested-questions";
import { Skeleton } from "@/components/ui/skeleton";
import { SUGGESTED_QUESTIONS } from "@/lib/suggested-questions";

interface ChatPanelProps {
  messages: ChatMessageType[];
  isResponding: boolean;
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onSelectSuggested: (question: string) => void;
  onCitationClick: (citation: Citation) => void;
  onStop: () => void;
  /** True until a PDF is loaded — the composer is locked until then. */
  hasDocument: boolean;
  isLoadingConversation?: boolean;
}

function ConversationSkeleton() {
  return (
    <div className="space-y-6">
      {[0, 1, 2].map((item) => (
        <div
          key={item}
          className={item === 1 ? "flex justify-end" : "flex justify-start"}
        >
          <div className="flex max-w-[82%] gap-3">
            <Skeleton className="size-8 rounded-full" />
            <div className="space-y-2 rounded-2xl border border-border bg-card p-4">
              <Skeleton className="h-3 w-56 max-w-[52vw]" />
              <Skeleton className="h-3 w-72 max-w-[58vw]" />
              <Skeleton className="h-3 w-40 max-w-[42vw]" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
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
  onStop,
  hasDocument,
  isLoadingConversation = false,
}: ChatPanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll as new messages / streamed content arrive.
  const lastMessage = messages[messages.length - 1];
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, lastMessage?.content, isResponding]);

  const showWelcome = hasDocument && messages.length === 0;

  return (
    <div className="flex h-full flex-col bg-card/30">
      {/* Transcript */}
      <div className="scrollbar-thin flex-1 space-y-6 overflow-y-auto p-4 sm:p-6">
        {isLoadingConversation ? <ConversationSkeleton /> : null}

        {!isLoadingConversation && !hasDocument ? (
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

        {!isLoadingConversation && showWelcome ? (
          <div className="aurora-panel rounded-2xl border border-border bg-card p-5">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium text-foreground">
              <Sparkles className="size-4 text-[color:var(--accent-cyan)]" />
              Welcome to DocMind
            </div>
            <p className="text-sm leading-relaxed text-muted-foreground">
              Your documents are ready. Ask anything about them — compare them,
              cross-reference them, or dig into a single file. Every answer
              streams in live with citations you can click to jump to the
              source page.
            </p>
          </div>
        ) : null}

        {!isLoadingConversation ? messages.map((message) => (
          <ChatMessage
            key={message.id}
            message={message}
            onCitationClick={onCitationClick}
            onFollowUp={onSelectSuggested}
          />
        )) : null}

        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div className="space-y-3 border-t border-border bg-card/60 p-4">
        {hasDocument && messages.length === 0 && !isLoadingConversation ? (
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
          disabled={!hasDocument || isResponding || isLoadingConversation}
          isStreaming={isResponding}
          onStop={onStop}
          placeholder={
            hasDocument
              ? "Ask a question about your documents…"
              : "Upload a PDF to start chatting…"
          }
        />
      </div>
    </div>
  );
}
