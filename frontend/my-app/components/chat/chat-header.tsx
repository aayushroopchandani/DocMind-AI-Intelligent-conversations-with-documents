"use client";

import Link from "next/link";
import { BrainCircuit, FileText, Upload, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AuthenticatedUserMenu } from "@/components/auth/authenticated-user-menu";
import type { PdfDocumentInfo } from "@/lib/types";

interface ChatHeaderProps {
  document: PdfDocumentInfo | null;
  onUploadAnother: () => void;
}

/** Top bar for the chat workspace: brand, filename, and account controls. */
export function ChatHeader({ document, onUploadAnother }: ChatHeaderProps) {
  return (
    <header className="flex items-center justify-between gap-3 border-b border-border bg-card/50 px-4 py-2.5 backdrop-blur">
      <div className="flex min-w-0 items-center gap-3">
        <Link
          href="/"
          className="flex shrink-0 items-center gap-2 text-sm font-semibold tracking-tight text-foreground"
        >
          <BrainCircuit className="size-5" />
          <span className="hidden sm:inline">DocMind</span>
        </Link>

        {document ? (
          <div className="flex min-w-0 items-center gap-1.5 rounded-lg border border-border bg-background/60 px-2.5 py-1">
            <FileText className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate text-xs text-foreground" title={document.name}>
              {document.name}
            </span>
          </div>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-1.5">
        {document ? (
          <Button
            variant="outline"
            size="sm"
            onClick={onUploadAnother}
            className="gap-1.5"
            data-icon="inline-start"
          >
            <Upload className="size-3.5" />
            <span className="hidden sm:inline">Upload another</span>
          </Button>
        ) : null}

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
