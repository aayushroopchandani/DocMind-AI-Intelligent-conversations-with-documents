"use client";

import type { ReactElement } from "react";
import { Database, FileSpreadsheet, Import } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { notifyPendingFeature } from "@/lib/data-analysis/feedback";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * "New" menu for the explorer. Only blank spreadsheets work in this
 * milestone; import and data sources stay visible with honest feedback.
 */
export function NewArtifactMenu({ trigger }: { trigger: ReactElement }) {
  const { actions } = useWorkspace();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={trigger} />
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel>Add to workspace</DropdownMenuLabel>
        <DropdownMenuItem onClick={actions.createSpreadsheet}>
          <FileSpreadsheet />
          New blank spreadsheet
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => notifyPendingFeature("import")}>
          <Import />
          Import spreadsheet
          <DropdownMenuShortcut>Soon</DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => notifyPendingFeature("dataSource")}>
          <Database />
          Add data source
          <DropdownMenuShortcut>Soon</DropdownMenuShortcut>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
