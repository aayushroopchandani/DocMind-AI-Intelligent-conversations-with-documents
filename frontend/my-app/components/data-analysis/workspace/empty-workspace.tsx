"use client";

import { FileSpreadsheet, FileUp, Import, Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MAX_PDF_UPLOAD_BATCH } from "@/lib/data-analysis/constants";
import { notifyPendingFeature } from "@/lib/data-analysis/feedback";
import { usePdfUpload } from "@/lib/data-analysis/use-pdf-upload";
import { PdfUploadInput } from "@/components/data-analysis/explorer/pdf-upload-input";
import { PdfDropZone } from "@/components/data-analysis/workspace/pdf/pdf-drop-zone";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/** Centre-workspace state when no artifact tab is open. */
export function EmptyWorkspace() {
  const { actions } = useWorkspace();
  const { inputRef, isAdding, openFilePicker, addFiles, handleInputChange } =
    usePdfUpload();

  return (
    <PdfDropZone
      onFiles={addFiles}
      className="flex h-full items-center justify-center p-6 animate-in fade-in duration-300"
    >
      <div className="flex w-full max-w-md flex-col items-center text-center">
        <div className="flex size-12 items-center justify-center rounded-xl border border-border bg-card text-[color:var(--accent-cyan)]">
          <FileSpreadsheet className="size-6" />
        </div>
        <h2 className="mt-4 text-base font-semibold text-foreground">
          Start a new analysis workspace
        </h2>
        <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
          Create a blank spreadsheet or open PDF documents. Spreadsheet file
          import and additional data sources will be connected in later
          milestones.
        </p>
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          <Button size="sm" onClick={actions.createSpreadsheet}>
            <Plus data-icon="inline-start" />
            New blank spreadsheet
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={isAdding}
            onClick={openFilePicker}
          >
            <FileUp data-icon="inline-start" />
            Upload PDF
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled
            onClick={() => notifyPendingFeature("import")}
          >
            <Import data-icon="inline-start" />
            Import spreadsheet
            <Badge variant="secondary" className="ml-1 text-[10px]">
              Soon
            </Badge>
          </Button>
        </div>
        <p className="mt-4 text-xs text-muted-foreground/70">
          Or drop up to {MAX_PDF_UPLOAD_BATCH} PDFs here — they are stored
          locally in this browser, not uploaded.
        </p>
      </div>
      <PdfUploadInput inputRef={inputRef} onChange={handleInputChange} />
    </PdfDropZone>
  );
}
