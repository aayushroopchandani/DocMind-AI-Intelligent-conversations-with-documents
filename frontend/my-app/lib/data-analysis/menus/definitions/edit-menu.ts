import {
  ClipboardPaste,
  Copy,
  Eraser,
  MousePointerSquareDashed,
  Redo2,
  Scissors,
  Trash2,
  Undo2,
} from "lucide-react";
import { notifyClipboardBlocked } from "@/lib/data-analysis/feedback";
import type { MenuDefinition } from "@/lib/data-analysis/menus/menu-types";
import {
  clearValues,
  copySelection,
  cutSelection,
  pasteIntoSelection,
  redo,
  selectAll,
  selectDataRange,
  undo,
} from "@/lib/data-analysis/sheet/edit-commands";
import { clearFormatting } from "@/lib/data-analysis/sheet/format-commands";
import {
  deleteCellsShiftLeft,
  deleteCellsShiftUp,
  deleteColumns,
  deleteRows,
} from "@/lib/data-analysis/sheet/structure-commands";

/**
 * Edit menu — entirely local. Clipboard entries route through Univer's own
 * clipboard service, which the browser may refuse without a direct key
 * press; that refusal is reported rather than swallowed.
 */
export const editMenu: MenuDefinition = {
  id: "edit",
  label: "Edit",
  build: (context) => {
    const disabled = !context.sheetReady;

    const runClipboard = (action: string, run: () => Promise<boolean>) => {
      void run().then((ok) => {
        if (!ok) notifyClipboardBlocked(action);
      });
    };

    return [
      {
        kind: "item",
        id: "undo",
        label: "Undo",
        icon: Undo2,
        shortcut: "⌘Z",
        disabled,
        onSelect: undo,
      },
      {
        kind: "item",
        id: "redo",
        label: "Redo",
        icon: Redo2,
        shortcut: "⇧⌘Z",
        disabled,
        onSelect: redo,
      },
      { kind: "separator", id: "sep-clipboard" },

      {
        kind: "item",
        id: "cut",
        label: "Cut",
        icon: Scissors,
        shortcut: "⌘X",
        disabled,
        onSelect: () => runClipboard("Cut", cutSelection),
      },
      {
        kind: "item",
        id: "copy",
        label: "Copy",
        icon: Copy,
        shortcut: "⌘C",
        disabled,
        onSelect: () => runClipboard("Copy", copySelection),
      },
      {
        kind: "item",
        id: "paste",
        label: "Paste",
        icon: ClipboardPaste,
        shortcut: "⌘V",
        disabled,
        onSelect: () => runClipboard("Paste", pasteIntoSelection),
      },
      { kind: "separator", id: "sep-delete" },

      {
        kind: "item",
        id: "clear-values",
        label: "Delete values",
        icon: Eraser,
        shortcut: "Del",
        disabled,
        onSelect: clearValues,
      },
      {
        kind: "item",
        id: "clear-format",
        label: "Clear formatting",
        disabled,
        onSelect: clearFormatting,
      },
      {
        kind: "submenu",
        id: "delete",
        label: "Delete",
        icon: Trash2,
        disabled,
        items: [
          {
            kind: "item",
            id: "delete-rows",
            label: "Selected rows",
            onSelect: deleteRows,
          },
          {
            kind: "item",
            id: "delete-columns",
            label: "Selected columns",
            onSelect: deleteColumns,
          },
          { kind: "separator", id: "sep-cells" },
          {
            kind: "item",
            id: "delete-cells-up",
            label: "Cells, shift up",
            onSelect: deleteCellsShiftUp,
          },
          {
            kind: "item",
            id: "delete-cells-left",
            label: "Cells, shift left",
            onSelect: deleteCellsShiftLeft,
          },
        ],
      },
      { kind: "separator", id: "sep-select" },

      {
        kind: "item",
        id: "select-all",
        label: "Select all",
        icon: MousePointerSquareDashed,
        shortcut: "⌘A",
        disabled,
        onSelect: selectAll,
      },
      {
        kind: "item",
        id: "select-data",
        label: "Select data range",
        disabled,
        onSelect: selectDataRange,
      },
    ];
  },
};
