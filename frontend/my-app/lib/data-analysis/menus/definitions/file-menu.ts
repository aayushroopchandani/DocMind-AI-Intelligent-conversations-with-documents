import {
  Clock,
  Download,
  FilePlus2,
  FileSpreadsheet,
  FileUp,
  Import,
  PanelTopClose,
  Save,
  Share2,
  Table2,
  TextCursorInput,
} from "lucide-react";
import {
  notifyBackendPending,
  notifyCsvExported,
  notifyNothingToExport,
} from "@/lib/data-analysis/feedback";
import type { MenuDefinition } from "@/lib/data-analysis/menus/menu-types";
import { exportActiveSheetToCsv } from "@/lib/data-analysis/sheet/csv-export";

/**
 * File menu.
 *
 * Everything the browser can do on its own is live; anything that needs the
 * analysis backend (XLSX/CSV *import*, XLSX and PDF export, sharing) stays
 * visible and says so when clicked, so the roadmap is legible without
 * pretending the server is there.
 */
export const fileMenu: MenuDefinition = {
  id: "file",
  label: "File",
  build: (context) => [
    { kind: "label", id: "new-label", label: "New" },
    {
      kind: "item",
      id: "new-sheet",
      // One workbook per workspace: once it exists, "new" means a new sheet
      // inside it rather than a second file to keep track of.
      label: context.hasWorkbook
        ? "New sheet in workbook"
        : "New blank spreadsheet",
      icon: context.hasWorkbook ? Table2 : FilePlus2,
      onSelect: context.actions.createSpreadsheet,
    },
    {
      kind: "item",
      id: "upload-pdf",
      label: "Upload PDF…",
      icon: FileUp,
      onSelect: context.openPdfPicker,
    },
    { kind: "separator", id: "sep-project" },

    {
      kind: "item",
      id: "save",
      label: "Save to this browser",
      icon: Save,
      shortcut: "⌘S",
      onSelect: context.saveNow,
    },
    {
      kind: "item",
      id: "rename-project",
      label: "Rename project…",
      icon: TextCursorInput,
      onSelect: context.focusProjectName,
    },
    {
      kind: "item",
      id: "history",
      label: "Run history",
      icon: Clock,
      onSelect: () => context.ui.setHistoryOpen(true),
    },
    { kind: "separator", id: "sep-transfer" },

    {
      kind: "item",
      id: "export-csv",
      label: "Download this sheet (.csv)",
      icon: Download,
      disabled: !context.sheetReady,
      onSelect: () => {
        const result = exportActiveSheetToCsv(context.workbookName);
        if (result) notifyCsvExported(result.fileName);
        else notifyNothingToExport();
      },
    },
    {
      kind: "item",
      id: "export-xlsx",
      label: "Export as XLSX",
      icon: FileSpreadsheet,
      pending: true,
      onSelect: () => notifyBackendPending("XLSX export"),
    },
    {
      kind: "item",
      id: "import",
      label: "Import spreadsheet…",
      icon: Import,
      pending: true,
      onSelect: () => notifyBackendPending("Spreadsheet import"),
    },
    {
      kind: "item",
      id: "share",
      label: "Share…",
      icon: Share2,
      pending: true,
      onSelect: () => notifyBackendPending("Sharing"),
    },
    { kind: "separator", id: "sep-close" },

    {
      kind: "item",
      id: "close",
      label: "Close document",
      icon: PanelTopClose,
      disabled: !context.activeArtifact,
      onSelect: () => {
        if (context.activeArtifact) {
          context.actions.closeTab(context.activeArtifact.id);
        }
      },
    },
    {
      kind: "note",
      id: "note",
      text: "CSV download runs in this browser. XLSX transfer and sharing need the analysis backend.",
    },
  ],
};
