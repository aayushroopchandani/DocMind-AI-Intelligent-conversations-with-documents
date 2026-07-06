"use client";

import { useCallback, useRef, useState, type DragEvent } from "react";
import { UploadCloud, FileText, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Maximum accepted PDF size (bytes). */
const MAX_SIZE_BYTES = 25 * 1024 * 1024; // 25 MB

interface PdfUploaderProps {
  /** Receives a validated PDF file. Parent owns the object URL lifecycle. */
  onFileSelected: (file: File) => void;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/**
 * Empty-state drag-and-drop area shown before a PDF is loaded.
 * Validates that the file is a PDF and within the size limit.
 */
export function PdfUploader({ onFileSelected }: PdfUploaderProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const validateAndSelect = useCallback(
    (file: File | undefined) => {
      if (!file) return;

      const isPdf =
        file.type === "application/pdf" ||
        file.name.toLowerCase().endsWith(".pdf");
      if (!isPdf) {
        setError("That file isn’t a PDF. Please choose a .pdf file.");
        return;
      }
      if (file.size > MAX_SIZE_BYTES) {
        setError(
          `File is too large (${formatBytes(file.size)}). Max size is ${formatBytes(MAX_SIZE_BYTES)}.`,
        );
        return;
      }

      setError(null);
      onFileSelected(file);
    },
    [onFileSelected],
  );

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    validateAndSelect(e.dataTransfer.files?.[0]);
  };

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col items-center px-4">
      <div
        role="button"
        tabIndex={0}
        aria-label="Upload a PDF"
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        className={cn(
          "group flex w-full cursor-pointer flex-col items-center rounded-3xl border border-dashed border-border bg-card/40 px-8 py-14 text-center transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          isDragging
            ? "scale-[1.01] border-foreground/50 bg-accent"
            : "hover:border-foreground/30 hover:bg-card/70",
        )}
      >
        <div
          className={cn(
            "mb-5 inline-flex size-16 items-center justify-center rounded-2xl border border-border bg-background/60 text-foreground transition-transform",
            isDragging ? "scale-110" : "group-hover:scale-105",
          )}
        >
          <UploadCloud className="size-7" />
        </div>

        <h2 className="text-lg font-semibold tracking-tight text-foreground">
          {isDragging ? "Drop your PDF here" : "Upload a PDF to get started"}
        </h2>
        <p className="mt-2 max-w-sm text-sm text-muted-foreground">
          Drag and drop your document here, or choose a file. It stays in your
          browser — nothing is uploaded or stored.
        </p>

        <Button className="mt-6 gap-2" data-icon="inline-start" size="lg">
          <FileText className="size-4" />
          Choose PDF
        </Button>

        <p className="mt-4 text-xs text-muted-foreground">
          PDF only · up to {formatBytes(MAX_SIZE_BYTES)}
        </p>

        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="sr-only"
          onChange={(e) => validateAndSelect(e.target.files?.[0])}
        />
      </div>

      {error ? (
        <div
          role="alert"
          className="mt-4 flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-2.5 text-sm text-destructive"
        >
          <AlertCircle className="size-4 shrink-0" />
          {error}
        </div>
      ) : null}
    </div>
  );
}

export { formatBytes };
