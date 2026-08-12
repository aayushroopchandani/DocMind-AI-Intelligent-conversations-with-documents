"use client";

import type { ReactElement } from "react";
import { Database, FileSpreadsheet, FileUp, Import, Table2 } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MAX_PDF_UPLOAD_BATCH } from "@/lib/data-analysis/constants";
import { notifyPendingFeature } from "@/lib/data-analysis/feedback";
import { usePdfUpload } from "@/lib/data-analysis/use-pdf-upload";
import { primarySpreadsheet } from "@/lib/data-analysis/workspace-state";
import { PdfUploadInput } from "@/components/data-analysis/explorer/pdf-upload-input";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * "Add to workspace" menu behind every "+" in the UI.
 *
 * The workspace keeps one workbook, so the blank-spreadsheet entry adds a
 * *sheet* inside it once it exists. The label says which of the two will
 * happen rather than promising a new file and quietly doing something else.
 */
export function NewArtifactMenu({ trigger }: { trigger: ReactElement }) {
  const { state, actions } = useWorkspace();
  const { inputRef, openFilePicker, handleInputChange } = usePdfUpload();

  const hasWorkbook = Boolean(primarySpreadsheet(state));

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={trigger} />
        <DropdownMenuContent align="end" className="w-64">
          {/* Base UI requires GroupLabel to live inside a Group. */}
          <DropdownMenuGroup>
            <DropdownMenuLabel>Add to workspace</DropdownMenuLabel>
            <DropdownMenuItem onClick={actions.createSpreadsheet}>
              {hasWorkbook ? <Table2 /> : <FileSpreadsheet />}
              {hasWorkbook ? "New blank sheet" : "New blank spreadsheet"}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={openFilePicker}>
              <FileUp />
              Upload PDF
              <DropdownMenuShortcut>
                Up to {MAX_PDF_UPLOAD_BATCH}
              </DropdownMenuShortcut>
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
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
          </DropdownMenuGroup>
          <p className="px-2 pt-1 pb-1 text-[11px] leading-relaxed text-muted-foreground/70">
            {hasWorkbook
              ? "This workspace keeps one workbook — new surfaces are sheets inside it."
              : "XLSX, XLS and CSV import will be connected through the backend in a later milestone."}
          </p>
        </DropdownMenuContent>
      </DropdownMenu>
      <PdfUploadInput inputRef={inputRef} onChange={handleInputChange} />
    </>
  );
}
