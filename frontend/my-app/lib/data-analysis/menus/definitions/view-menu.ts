import {
  Expand,
  Grid3x3,
  PanelLeft,
  PanelRight,
  Rows3,
  Search,
  Snowflake,
  Wrench,
} from "lucide-react";
import type { MenuDefinition } from "@/lib/data-analysis/menus/menu-types";
import {
  areGridlinesVisible,
  freezeColumns,
  freezeRows,
  freezeToSelection,
  getFrozenColumns,
  getFrozenRows,
  getZoom,
  isFullscreen,
  isUiPartVisible,
  setZoom,
  toggleFullscreen,
  toggleGridlines,
  toggleUiPart,
  unfreeze,
  ZOOM_STEPS,
} from "@/lib/data-analysis/sheet/view-commands";

/**
 * View menu — workspace panels plus the spreadsheet's own presentation.
 * Every entry is local state: panel sizes live in the workspace layout,
 * the rest lives on the Univer worksheet.
 */
export const viewMenu: MenuDefinition = {
  id: "view",
  label: "View",
  build: (context) => {
    const disabled = !context.sheetReady;
    const zoom = context.sheetReady ? getZoom() : 1;
    const frozenRows = context.sheetReady ? getFrozenRows() : 0;
    const frozenColumns = context.sheetReady ? getFrozenColumns() : 0;

    return [
      { kind: "label", id: "panels-label", label: "Panels" },
      {
        kind: "checkbox",
        id: "files-panel",
        label: "Files",
        icon: PanelLeft,
        checked: !context.layout.leftCollapsed,
        onSelect: () =>
          context.updateLayout({ leftCollapsed: !context.layout.leftCollapsed }),
      },
      {
        kind: "checkbox",
        id: "analyst-panel",
        label: "AI analyst",
        icon: PanelRight,
        checked: !context.layout.rightCollapsed,
        onSelect: () =>
          context.updateLayout({
            rightCollapsed: !context.layout.rightCollapsed,
          }),
      },
      {
        kind: "item",
        id: "run-history",
        label: "Run history",
        icon: Search,
        onSelect: () => context.ui.setHistoryOpen(true),
      },
      { kind: "separator", id: "sep-sheet" },

      { kind: "label", id: "sheet-label", label: "Spreadsheet" },
      {
        kind: "checkbox",
        id: "gridlines",
        label: "Gridlines",
        icon: Grid3x3,
        checked: context.sheetReady ? areGridlinesVisible() : true,
        disabled,
        onSelect: toggleGridlines,
      },
      {
        // Folds Univer's whole ribbon — tab strip and toolbar — through the
        // workspace layout, so the menu, the app-bar chevron and the saved
        // layout all describe the same thing.
        kind: "checkbox",
        id: "toolbar",
        label: "Formatting toolbar",
        icon: Wrench,
        checked: !context.layout.ribbonCollapsed,
        disabled,
        onSelect: () =>
          context.updateLayout({
            ribbonCollapsed: !context.layout.ribbonCollapsed,
          }),
      },
      {
        kind: "checkbox",
        id: "footer",
        label: "Sheet tabs and status bar",
        icon: Rows3,
        checked: context.sheetReady ? isUiPartVisible("footer") : true,
        disabled,
        onSelect: () => toggleUiPart("footer"),
      },
      {
        kind: "submenu",
        id: "zoom",
        label: `Zoom — ${Math.round(zoom * 100)}%`,
        disabled,
        items: ZOOM_STEPS.map((step) => ({
          kind: "checkbox" as const,
          id: `zoom-${step}`,
          label: `${Math.round(step * 100)}%`,
          checked: Math.abs(step - zoom) < 0.01,
          onSelect: () => setZoom(step),
        })),
      },
      {
        kind: "submenu",
        id: "freeze",
        label: "Freeze",
        icon: Snowflake,
        disabled,
        items: [
          { kind: "label", id: "freeze-rows-label", label: "Rows" },
          {
            kind: "checkbox",
            id: "freeze-rows-0",
            label: "No rows",
            checked: frozenRows === 0,
            onSelect: () => freezeRows(0),
          },
          {
            kind: "checkbox",
            id: "freeze-rows-1",
            label: "1 row",
            checked: frozenRows === 1,
            onSelect: () => freezeRows(1),
          },
          {
            kind: "checkbox",
            id: "freeze-rows-2",
            label: "2 rows",
            checked: frozenRows === 2,
            onSelect: () => freezeRows(2),
          },
          { kind: "separator", id: "sep-freeze-columns" },
          { kind: "label", id: "freeze-columns-label", label: "Columns" },
          {
            kind: "checkbox",
            id: "freeze-columns-0",
            label: "No columns",
            checked: frozenColumns === 0,
            onSelect: () => freezeColumns(0),
          },
          {
            kind: "checkbox",
            id: "freeze-columns-1",
            label: "1 column",
            checked: frozenColumns === 1,
            onSelect: () => freezeColumns(1),
          },
          {
            kind: "checkbox",
            id: "freeze-columns-2",
            label: "2 columns",
            checked: frozenColumns === 2,
            onSelect: () => freezeColumns(2),
          },
          { kind: "separator", id: "sep-freeze-selection" },
          {
            kind: "item",
            id: "freeze-selection",
            label: "Up to current cell",
            onSelect: freezeToSelection,
          },
          {
            kind: "item",
            id: "unfreeze",
            label: "Unfreeze all",
            onSelect: unfreeze,
          },
        ],
      },
      { kind: "separator", id: "sep-fullscreen" },

      {
        kind: "checkbox",
        id: "fullscreen",
        label: "Full screen",
        icon: Expand,
        checked: isFullscreen(),
        onSelect: toggleFullscreen,
      },
    ];
  },
};
