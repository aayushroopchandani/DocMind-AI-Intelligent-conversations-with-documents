import {
  ArrowDownToLine,
  ArrowRightToLine,
  BarChart3,
  FileUp,
  Image,
  Link2,
  MessageSquare,
  Sigma,
  Table2,
} from "lucide-react";
import { notifyBackendPending } from "@/lib/data-analysis/feedback";
import { applyFunction } from "@/lib/data-analysis/menus/menu-actions";
import type { MenuDefinition } from "@/lib/data-analysis/menus/menu-types";
import { AUTOSUM_FUNCTIONS } from "@/lib/data-analysis/sheet/formula-commands";
import {
  duplicateActiveWorksheet,
  insertCellsShiftDown,
  insertCellsShiftRight,
  insertColumnsLeft,
  insertColumnsRight,
  insertRowsAbove,
  insertRowsBelow,
} from "@/lib/data-analysis/sheet/structure-commands";

/**
 * Insert menu.
 *
 * Structural inserts (rows, columns, cells, sheets) are pure Univer and work
 * now. Charts, images, links and comments are the ones that need either the
 * analysis backend or Univer's commercial drawing plugins, so they follow the
 * File menu's pattern: visible, honest, and explained on click.
 */
export const insertMenu: MenuDefinition = {
  id: "insert",
  label: "Insert",
  build: (context) => {
    const disabled = !context.sheetReady;

    return [
      { kind: "label", id: "structure-label", label: "Sheet structure" },
      {
        kind: "item",
        id: "rows-above",
        label: "Rows above",
        disabled,
        onSelect: insertRowsAbove,
      },
      {
        kind: "item",
        id: "rows-below",
        label: "Rows below",
        icon: ArrowDownToLine,
        disabled,
        onSelect: insertRowsBelow,
      },
      {
        kind: "item",
        id: "columns-left",
        label: "Columns left",
        disabled,
        onSelect: insertColumnsLeft,
      },
      {
        kind: "item",
        id: "columns-right",
        label: "Columns right",
        icon: ArrowRightToLine,
        disabled,
        onSelect: insertColumnsRight,
      },
      {
        kind: "submenu",
        id: "cells",
        label: "Cells",
        disabled,
        items: [
          {
            kind: "item",
            id: "cells-down",
            label: "Insert, shift down",
            onSelect: insertCellsShiftDown,
          },
          {
            kind: "item",
            id: "cells-right",
            label: "Insert, shift right",
            onSelect: insertCellsShiftRight,
          },
        ],
      },
      { kind: "separator", id: "sep-sheets" },

      {
        kind: "item",
        id: "new-sheet",
        label: "New sheet",
        icon: Table2,
        onSelect: context.actions.createSpreadsheet,
      },
      {
        kind: "item",
        id: "duplicate-sheet",
        label: "Duplicate sheet",
        disabled,
        onSelect: duplicateActiveWorksheet,
      },
      { kind: "separator", id: "sep-content" },

      { kind: "label", id: "content-label", label: "Content" },
      {
        kind: "submenu",
        id: "function",
        label: "Function",
        icon: Sigma,
        disabled,
        items: AUTOSUM_FUNCTIONS.map((name) => ({
          kind: "item" as const,
          id: `fn-${name}`,
          label: name,
          onSelect: () => applyFunction(name),
        })),
      },
      {
        kind: "item",
        id: "pdf",
        label: "PDF document…",
        icon: FileUp,
        onSelect: context.openPdfPicker,
      },
      {
        kind: "item",
        id: "chart",
        label: "Chart",
        icon: BarChart3,
        pending: true,
        onSelect: () => notifyBackendPending("Charts"),
      },
      {
        kind: "item",
        id: "image",
        label: "Image",
        icon: Image,
        pending: true,
        onSelect: () => notifyBackendPending("Images"),
      },
      {
        kind: "item",
        id: "link",
        label: "Link",
        icon: Link2,
        pending: true,
        onSelect: () => notifyBackendPending("Links"),
      },
      {
        kind: "item",
        id: "comment",
        label: "Comment",
        icon: MessageSquare,
        pending: true,
        onSelect: () => notifyBackendPending("Comments"),
      },
      {
        kind: "note",
        id: "note",
        text: "Charts, images, links and comments arrive with the analysis backend.",
      },
    ];
  },
};
