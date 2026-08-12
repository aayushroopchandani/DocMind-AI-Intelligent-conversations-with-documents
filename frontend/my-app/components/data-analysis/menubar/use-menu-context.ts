"use client";

import { useMemo } from "react";
import type { MenuContext } from "@/lib/data-analysis/menus/menu-types";
import { focusProjectName } from "@/lib/data-analysis/project-name-focus";
import {
  activeArtifact,
  primarySpreadsheet,
} from "@/lib/data-analysis/workspace-state";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * Assembles the context every menu definition is built against.
 *
 * Deliberately holds no live spreadsheet state (bold, zoom, gridlines…):
 * menus read that straight off the Univer facade as they open, so this
 * object stays stable and the app bar does not re-render on every keystroke
 * in the grid.
 */
export function useMenuContext(openPdfPicker: () => void): MenuContext {
  const { state, layout, updateLayout, actions, ui } = useWorkspace();

  const active = activeArtifact(state);
  const workbook = primarySpreadsheet(state);
  const sheetReady = state.univerReady && active?.type === "spreadsheet";

  return useMemo<MenuContext>(
    () => ({
      sheetReady,
      hasWorkbook: Boolean(workbook),
      activeArtifact: active,
      workbookName: workbook?.name ?? state.project.name,
      layout,
      updateLayout,
      actions,
      ui,
      openPdfPicker,
      saveNow: actions.saveNow,
      focusProjectName,
    }),
    [
      sheetReady,
      workbook,
      active,
      state.project.name,
      layout,
      updateLayout,
      actions,
      ui,
      openPdfPicker,
    ],
  );
}
