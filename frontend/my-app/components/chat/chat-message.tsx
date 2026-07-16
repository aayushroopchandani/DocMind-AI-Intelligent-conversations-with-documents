"use client";

import { memo } from "react";
import Link from "next/link";
import {
  ArrowRight,
  CircleAlert,
  GraduationCap,
  Sparkles,
  User,
  Zap,
} from "lucide-react";
import type { ChatMessage as ChatMessageType, Citation } from "@/lib/types";
import { CitationGroup } from "@/components/chat/citation-card";
import { StreamingMarkdown } from "@/components/chat/streaming-markdown";
import { quizHref } from "@/lib/quiz-session";
import { cn } from "@/lib/utils";

interface ChatMessageProps {
  message: ChatMessageType;
  onCitationClick: (citation: Citation) => void;
  /** Sends a follow-up question suggested by the assistant. */
  onFollowUp?: (question: string) => void;
}

/**
 * Renders a single user or assistant message. Memoized so that during token
 * streaming only the actively updating message re-renders, not the whole
 * transcript.
 */
export const ChatMessage = memo(function ChatMessage({
  message,
  onCitationClick,
  onFollowUp,
}: ChatMessageProps) {
  const isUser = message.role === "user";
  const isStreaming = message.status === "streaming";
  const showStatus = isStreaming && !message.content && message.statusText;

  // Group citations by document for the sources panel.
  const groups = new Map<string, Citation[]>();
  for (const citation of message.citations ?? []) {
    const list = groups.get(citation.documentId) ?? [];
    list.push(citation);
    groups.set(citation.documentId, list);
  }

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
          isUser
            ? "bg-primary text-primary-foreground"
            : "ai-avatar text-foreground",
        )}
      >
        {isUser ? <User className="size-4" /> : <Sparkles className="size-4" />}
      </div>

      <div className={cn("flex min-w-0 max-w-[85%] flex-col gap-2", isUser && "items-end")}>
        {message.quiz && !isUser ? (
          <QuizReadyCard quiz={message.quiz} />
        ) : (
          <div
            className={cn(
              "rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
              isUser
                ? "rounded-br-sm bg-primary text-primary-foreground"
                : "rounded-bl-sm border border-border bg-card text-foreground",
              !isUser && isStreaming && "streaming-border",
            )}
          >
            {isUser ? (
              message.content
            ) : showStatus ? (
              <span className="status-shimmer text-sm">{message.statusText}</span>
            ) : (
              <StreamingMarkdown
                content={message.content}
                isStreaming={isStreaming}
              />
            )}
          </div>
        )}

        {message.status === "error" ? (
          <p className="flex items-center gap-1.5 text-xs text-destructive">
            <CircleAlert className="size-3.5" />
            Generation failed. Please try again.
          </p>
        ) : null}

        {!isUser && groups.size > 0 && !isStreaming ? (
          <div className="w-full space-y-2.5">
            <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Sources
            </span>
            {Array.from(groups.entries()).map(([documentId, citations]) => (
              <CitationGroup
                key={documentId}
                documentName={citations[0].documentName}
                citations={citations}
                contribution={message.structured?.contributions.find(
                  (c) => c.documentId === documentId,
                )}
                onNavigate={onCitationClick}
              />
            ))}
          </div>
        ) : null}

        {!isUser &&
        !isStreaming &&
        onFollowUp &&
        message.structured &&
        message.structured.followUpQuestions.length > 0 ? (
          <div className="flex w-full flex-wrap gap-1.5">
            {message.structured.followUpQuestions.map((question) => (
              <button
                key={question}
                type="button"
                onClick={() => onFollowUp(question)}
                className="rounded-full border border-[color:var(--accent-violet)]/30 bg-[color:var(--accent-violet)]/10 px-2.5 py-1 text-left text-[11px] text-foreground/90 transition-colors hover:border-[color:var(--accent-violet)]/60 hover:bg-[color:var(--accent-violet)]/20"
              >
                {question}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
});

const MODE_DETAILS = {
  practice: { label: "Practice quiz", icon: Sparkles },
  rapid_fire: { label: "Rapid-fire quiz", icon: Zap },
  exam_mode: { label: "Exam quiz", icon: GraduationCap },
} as const;

function QuizReadyCard({ quiz }: { quiz: NonNullable<ChatMessageType["quiz"]> }) {
  const details = MODE_DETAILS[quiz.mode];
  const ModeIcon = details.icon;

  return (
    <Link
      href={quizHref(quiz.quizId, quiz.mode)}
      className="animate-quiz-in group block min-w-64 rounded-2xl rounded-bl-sm border border-[color:var(--accent-violet)]/35 bg-card p-4 text-foreground shadow-sm transition-colors hover:border-[color:var(--accent-violet)]/65 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--accent-violet)]"
      aria-label={`Open ${details.label}`}
    >
      <div className="flex items-start gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-xl border border-[color:var(--accent-violet)]/30 bg-[color:var(--accent-violet)]/10 text-[color:var(--accent-violet)]">
          <ModeIcon className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold">Your quiz is ready</span>
          <span className="mt-0.5 block text-xs text-muted-foreground">
            {details.label}
            {quiz.numberOfQuestions
              ? ` · ${quiz.numberOfQuestions} questions`
              : ""}
          </span>
        </span>
        <ArrowRight className="mt-2.5 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-foreground" />
      </div>
    </Link>
  );
}

/** Animated three-dot indicator shown while the assistant is "thinking". */
export function TypingIndicator() {
  return (
    <div className="flex w-full gap-3">
      <div className="ai-avatar flex size-8 shrink-0 items-center justify-center rounded-full border border-border text-foreground">
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
