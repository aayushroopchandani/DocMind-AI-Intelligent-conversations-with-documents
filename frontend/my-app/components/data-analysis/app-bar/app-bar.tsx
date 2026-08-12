"use client";

import { PanelLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { usePdfUpload } from "@/lib/data-analysis/use-pdf-upload";
import { AppBarActions } from "@/components/data-analysis/app-bar/app-bar-actions";
import { AppBrand } from "@/components/data-analysis/app-bar/app-brand";
import { DocumentSwitcher } from "@/components/data-analysis/app-bar/document-switcher";
import { ProjectNameInput } from "@/components/data-analysis/app-bar/project-name-input";
import { SaveStatusPill } from "@/components/data-analysis/app-bar/save-status-pill";
import { PdfUploadInput } from "@/components/data-analysis/explorer/pdf-upload-input";
import {
  CompactMenuBar,
  MenuBar,
} from "@/components/data-analysis/menubar/menu-bar";
import { useMenuContext } from "@/components/data-analysis/menubar/use-menu-context";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * The single row of application chrome above the spreadsheet.
 *
 * Everything the old two-row header carried — brand, project identity, save
 * state, document tabs, global actions — now shares this row with the menu
 * bar, which is the layout every desktop spreadsheet uses and gives the grid
 * back a row of height. Univer's own ribbon and formula bar sit directly
 * beneath it, untouched.
 */
export function DataAnalysisAppBar() {
  const { state, actions, ui } = useWorkspace();
  const { inputRef, openFilePicker, handleInputChange } = usePdfUpload();
  const menuContext = useMenuContext(openFilePicker);

  const hasUnsavedEdits = state.artifacts.some((artifact) => artifact.isDirty);

  return (
    <header className="flex h-11 shrink-0 items-center gap-1 border-b border-border bg-card/40 px-2 backdrop-blur">
      {/* Below `lg` the explorer is an overlay sheet, not a column. */}
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Open file explorer"
              className="lg:hidden"
              onClick={() => ui.setExplorerSheetOpen(true)}
            >
              <PanelLeft />
            </Button>
          }
        />
        <TooltipContent>Files</TooltipContent>
      </Tooltip>

      <AppBrand />

      <Separator orientation="vertical" className="mx-1 hidden h-5! md:block" />

      <MenuBar context={menuContext} />
      <CompactMenuBar context={menuContext} />

      <Separator orientation="vertical" className="mx-1 h-5!" />

      <DocumentSwitcher />

      <ProjectNameInput
        // Keyed so external changes (hydration, agent renames) reset the
        // in-progress draft cleanly.
        key={state.project.name}
        name={state.project.name}
        onCommit={actions.setProjectName}
      />
      <SaveStatusPill
        status={state.saveStatus}
        hasUnsavedEdits={hasUnsavedEdits}
      />

      <AppBarActions />

      {/* Backs "File → Upload PDF…" and "Insert → PDF document…". */}
      <PdfUploadInput inputRef={inputRef} onChange={handleInputChange} />
    </header>
  );
}
