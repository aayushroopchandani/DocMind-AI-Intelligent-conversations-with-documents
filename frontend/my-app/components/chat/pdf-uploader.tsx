"use client";

import { useCallback, useRef, useState, type DragEvent } from "react";
import { UploadCloud, FileText, AlertCircle, Plus } from "lucide-react";
import SplitText from "@/components/SplitText";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { MAX_SIZE_BYTES, formatBytes, validatePdfFiles } from "@/lib/pdf";

interface PdfUploaderProps {
  /** Receives the validated PDF files (type + size checked, capped to `remaining`). */
  onFilesSelected: (files: File[]) => void;
  /** How many more PDFs can still be added to the chat. */
  remaining: number;
  /** Total per-chat limit (for copy). */
  maxFiles: number;
  /** Compact variant used as an inline "add more" dropzone. */
  compact?: boolean;
  /** External error (e.g. an upload failure) to surface below the dropzone. */
  externalError?: string | null;
}

/**
 * Premium drag-and-drop upload area. Supports selecting multiple PDFs at once,
 * validates type + size, and never silently drops files past the limit — it
 * accepts the allowed number and reports the rest.
 */
export function PdfUploader({
  onFilesSelected,
  remaining,
  maxFiles,
  compact = false,
  externalError = null,
}: PdfUploaderProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const atLimit = remaining <= 0;

  const validateAndSelect = useCallback(
    (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return;
      const { accepted, message } = validatePdfFiles(
        Array.from(fileList),
        remaining,
        maxFiles,
      );
      setError(message);
      if (accepted.length) onFilesSelected(accepted);
    },
    [onFilesSelected, remaining, maxFiles],
  );

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    if (atLimit) return;
    validateAndSelect(e.dataTransfer.files);
  };

  const openPicker = () => {
    if (atLimit) return;
    inputRef.current?.click();
  };

  const shownError = error ?? externalError;

  return (
    <div
      className={cn(
        "mx-auto flex w-full flex-col items-center px-4",
        compact ? "max-w-full" : "max-w-xl",
      )}
    >
      <div
        role="button"
        tabIndex={atLimit ? -1 : 0}
        aria-label="Upload PDFs"
        aria-disabled={atLimit}
        onClick={openPicker}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openPicker();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!atLimit) setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        className={cn(
          "group flex w-full flex-col items-center rounded-3xl border border-dashed border-border bg-card/40 text-center transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          compact ? "px-6 py-8" : "px-8 py-14",
          atLimit
            ? "cursor-not-allowed opacity-60"
            : "cursor-pointer",
          !atLimit && isDragging
            ? "scale-[1.01] border-foreground/50 bg-accent"
            : !atLimit && "hover:border-foreground/30 hover:bg-card/70",
        )}
      >
        <div
          className={cn(
            "mb-5 inline-flex items-center justify-center rounded-2xl border border-border bg-background/60 text-foreground transition-transform",
            compact ? "size-12" : "size-16",
            !atLimit && (isDragging ? "scale-110" : "group-hover:scale-105"),
          )}
        >
          {compact ? (
            <Plus className="size-6" />
          ) : (
            <UploadCloud className="size-7" />
          )}
        </div>

        {compact ? (
          <h2 className="text-base font-semibold tracking-tight text-foreground">
            {atLimit ? `Limit reached (${maxFiles} PDFs)` : "Add another PDF"}
          </h2>
        ) : (
          <SplitText
            key={isDragging ? "drop" : "idle"}
            text={
              isDragging
                ? "Drop your PDFs here"
                : "Upload documents to start a conversation"
            }
            tag="h2"
            className="text-lg font-semibold tracking-tight text-foreground sm:text-xl"
            splitType="words"
            delay={25}
            duration={0.6}
            from={{ opacity: 0, y: 20 }}
            to={{ opacity: 1, y: 0 }}
            threshold={0.1}
          />
        )}

        {!compact ? (
          <p className="mt-3 max-w-sm text-sm text-muted-foreground">
            Add up to {maxFiles} PDF files and ask questions across one or
            multiple documents. Files are uploaded securely to your account.
          </p>
        ) : null}

        {!atLimit ? (
          <Button
            className="mt-6 gap-2"
            data-icon="inline-start"
            size={compact ? "sm" : "lg"}
          >
            <FileText className="size-4" />
            Choose PDFs
          </Button>
        ) : null}

        <p className="mt-4 text-xs text-muted-foreground">
          PDF files only · Maximum {maxFiles} documents · up to{" "}
          {formatBytes(MAX_SIZE_BYTES)} each
        </p>

        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          className="sr-only"
          onChange={(e) => {
            validateAndSelect(e.target.files);
            // Reset so selecting the same file again re-triggers onChange.
            e.target.value = "";
          }}
        />
      </div>

      {shownError ? (
        <div
          role="alert"
          className="mt-4 flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-2.5 text-sm text-destructive"
        >
          <AlertCircle className="mt-0.5 size-4 shrink-0" />
          <span>{shownError}</span>
        </div>
      ) : null}
    </div>
  );
}
