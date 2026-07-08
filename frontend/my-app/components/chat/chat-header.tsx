"use client";

import Link from "next/link";
import { BrainCircuit, Files, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AuthenticatedUserMenu } from "@/components/auth/authenticated-user-menu";

interface ChatHeaderProps {
  /** Number of PDFs currently attached to the chat. */
  documentCount: number;
  /** Per-chat document limit. */
  maxFiles: number;
}

/** Top bar for the chat workspace: brand, document count, and account controls. */
export function ChatHeader({ documentCount, maxFiles }: ChatHeaderProps) {
  return (
    <header className="flex items-center justify-between gap-3 border-b border-border bg-card/50 px-4 py-2.5 backdrop-blur">
      <div className="flex min-w-0 items-center gap-3">
        <Link
          href="/"
          className="flex shrink-0 items-center gap-2 text-sm font-semibold tracking-tight text-foreground"
        >
          <span className="ai-avatar inline-flex size-7 items-center justify-center rounded-lg">
            <BrainCircuit className="size-4" />
          </span>
          <span className="hidden sm:inline">DocMind</span>
        </Link>

        {documentCount > 0 ? (
          <div className="flex min-w-0 items-center gap-1.5 rounded-lg border border-border bg-background/60 px-2.5 py-1">
            <Files className="size-3.5 shrink-0 text-[color:var(--accent-cyan)]" />
            <span className="text-xs tabular-nums text-foreground">
              Documents: {documentCount} / {maxFiles}
            </span>
          </div>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-1.5">
        <Button
          variant="ghost"
          size="sm"
          nativeButton={false}
          render={<Link href="/" />}
          className="gap-1.5 text-muted-foreground hover:text-foreground"
          data-icon="inline-start"
        >
          <ArrowLeft className="size-3.5" />
          <span className="hidden sm:inline">Home</span>
        </Button>

        <AuthenticatedUserMenu />
      </div>
    </header>
  );
}
