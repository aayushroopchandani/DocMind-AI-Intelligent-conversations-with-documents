"use client";

import { useState, type DragEvent, type ReactNode } from "react";
import { FileUp } from "lucide-react";
import { MAX_PDF_UPLOAD_BATCH } from "@/lib/data-analysis/constants";
import { dragEventHasFiles } from "@/lib/data-analysis/pdf/pdf-validation";
import { cn } from "@/lib/utils";

interface PdfDropZoneProps {
  onFiles: (files: readonly File[]) => void;
  children: ReactNode;
  className?: string;
}

/**
 * Wraps a region so dropped PDFs land in the workspace.
 *
 * The overlay only appears while a drag is actually over the region — the
 * workspace never permanently looks like an upload screen.
 */
export function PdfDropZone({ onFiles, children, className }: PdfDropZoneProps) {
  const [isOver, setIsOver] = useState(false);

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!dragEventHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setIsOver(true);
  };

  const handleDragLeave = (event: DragEvent<HTMLDivElement>) => {
    // Moving between children fires dragleave too; only a real exit counts.
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setIsOver(false);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!dragEventHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    setIsOver(false);
    onFiles(Array.from(event.dataTransfer.files));
  };

  return (
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={cn("relative", className)}
    >
      {children}
      {isOver ? (
        <div className="pointer-events-none absolute inset-3 z-10 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-[color:var(--accent-cyan)]/60 bg-background/85 animate-in fade-in duration-150">
          <FileUp className="size-6 text-[color:var(--accent-cyan)]" />
          <p className="text-sm font-medium text-foreground">
            Drop to add PDFs to this workspace
          </p>
          <p className="text-xs text-muted-foreground">
            Up to {MAX_PDF_UPLOAD_BATCH} files at a time · stored in this browser
          </p>
        </div>
      ) : null}
    </div>
  );
}
