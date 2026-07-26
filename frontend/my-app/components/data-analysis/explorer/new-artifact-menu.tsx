"use client";

import type { ReactElement } from "react";
import { Database, FileSpreadsheet, FileUp, Import } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MAX_PDF_UPLOAD_BATCH } from "@/lib/data-analysis/constants";
import { notifyPendingFeature } from "@/lib/data-analysis/feedback";
import { usePdfUpload } from "@/lib/data-analysis/use-pdf-upload";
import { PdfUploadInput } from "@/components/data-analysis/explorer/pdf-upload-input";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * "Add to workspace" menu.
 *
 * Blank spreadsheets and PDF upload work now. Spreadsheet import and data
 * sources stay visible but disabled so the roadmap is legible without
 * pretending the backend exists.
 */
export function NewArtifactMenu({ trigger }: { trigger: ReactElement }) {
  const { actions } = useWorkspace();
  const { inputRef, openFilePicker, handleInputChange } = usePdfUpload();

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={trigger} />
        <DropdownMenuContent align="end" className="w-64">
          <DropdownMenuLabel>Add to workspace</DropdownMenuLabel>
          <DropdownMenuItem onClick={openFilePicker}>
            <FileUp />
            Upload PDF
            <DropdownMenuShortcut>
              Up to {MAX_PDF_UPLOAD_BATCH}
            </DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={actions.createSpreadsheet}>
            <FileSpreadsheet />
            New blank spreadsheet
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled
            onClick={() => notifyPendingFeature("import")}
          >
            <Import />
            Import spreadsheet
            <DropdownMenuShortcut>Soon</DropdownMenuShortcut>
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled
            onClick={() => notifyPendingFeature("dataSource")}
          >
            <Database />
            Add data source
            <DropdownMenuShortcut>Soon</DropdownMenuShortcut>
          </DropdownMenuItem>
          <p className="px-2 pt-1 pb-1 text-[11px] leading-relaxed text-muted-foreground/70">
            XLSX, XLS and CSV import will be connected through the backend in a
            later milestone.
          </p>
        </DropdownMenuContent>
      </DropdownMenu>
      <PdfUploadInput inputRef={inputRef} onChange={handleInputChange} />
    </>
  );
}
