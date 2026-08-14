import { toast } from "sonner";
import { MAX_PDF_UPLOAD_BATCH } from "@/lib/data-analysis/constants";
import type { RejectedPdfFile } from "@/lib/data-analysis/pdf/pdf-types";

/**
 * Honest "not wired up yet" feedback for controls that must be visible now
 * but only gain behaviour once the data-analysis backend ships.
 */
export const PENDING_FEATURE_MESSAGES = {
  share: "Sharing will be available once the backend integration lands.",
  dataSource: "Data sources will be connected to the backend in a later milestone.",
  analyst: "The data-analysis backend is still being connected.",
} as const;

export type PendingFeature = keyof typeof PENDING_FEATURE_MESSAGES;

export function notifyPendingFeature(feature: PendingFeature): void {
  toast.info(PENDING_FEATURE_MESSAGES[feature]);
}

/**
 * Menu-bar version of the same promise. The label is the menu entry the
 * user clicked, so the toast names the thing they actually asked for
 * instead of a generic "not implemented".
 */
export function notifyBackendPending(label: string): void {
  toast.info(`${label} — backend pending.`, {
    description:
      "This action runs through the analysis backend, which is still being connected.",
  });
}

export function notifyStorageFull(): void {
  toast.error(
    "Local draft could not be saved — this browser's storage is full.",
  );
}

/* ------------------------------------------------------------------ */
/* Spreadsheet menu actions                                            */
/* ------------------------------------------------------------------ */

export function notifySheetAdded(sheetName: string | null): void {
  toast.success(
    sheetName ? `Added “${sheetName}” to the workbook.` : "Sheet added.",
    { description: "This workspace keeps one workbook — extra surfaces are sheets inside it." },
  );
}

export function notifyWorkbookSaved(): void {
  toast.success("Workbook saved to this browser.");
}

export function notifyCsvExported(fileName: string): void {
  toast.success("Sheet downloaded as CSV.", { description: fileName });
}

export function notifyNothingToExport(): void {
  toast.info("This sheet has no data to export yet.");
}

export function notifyClipboardBlocked(action: string): void {
  toast.warning(`${action} was blocked by the browser.`, {
    description:
      "Clipboard access needs a direct key press — use the shortcut inside the grid instead.",
  });
}

/* ------------------------------------------------------------------ */
/* Spreadsheet import and export                                       */
/* ------------------------------------------------------------------ */

export function notifySpreadsheetImported(
  fileName: string,
  sheetCount: number,
  cellCount: number,
): void {
  toast.success(`Imported “${fileName}”.`, {
    description: `${sheetCount} sheet${sheetCount === 1 ? "" : "s"} · ${cellCount.toLocaleString()} cells added to this workbook.`,
  });
}

/**
 * Reports what the converter could not carry across. Silence would be worse:
 * a chart or a validation rule that vanished without a word looks like data
 * loss, and the user cannot tell it was expected.
 */
export function notifyImportWarnings(
  warnings: readonly { message: string; sheet?: string | null }[],
): void {
  if (warnings.length === 0) return;
  toast.warning(
    warnings.length === 1
      ? "One thing did not come across."
      : `${warnings.length} things did not come across.`,
    {
      description: warnings
        .slice(0, 4)
        .map((item) => (item.sheet ? `${item.sheet}: ${item.message}` : item.message))
        .join("\n"),
      duration: 8000,
    },
  );
}

export function notifySpreadsheetExported(fileName: string): void {
  toast.success("Workbook downloaded.", { description: fileName });
}

export function notifySpreadsheetTransferFailed(
  action: "import" | "export",
  reason: string,
): void {
  toast.error(
    action === "import"
      ? "That spreadsheet could not be imported."
      : "The workbook could not be exported.",
    { description: reason },
  );
}

export function notifyLastSheet(): void {
  toast.info("A workbook needs at least one sheet.");
}

/**
 * Reports where a formula landed. With no range to work on, the formula is
 * still written but says so — a bare `=SUM()` renders as `#N/A`, and an
 * unexplained error in a cell is worse than a sentence about it.
 */
export function notifyFormulaInserted(
  functionName: string,
  cell: string,
  reference: string,
): void {
  if (reference) {
    toast.success(`${cell} = ${functionName}(${reference})`);
    return;
  }
  toast.info(`Wrote =${functionName}() into ${cell}.`, {
    description:
      "Nothing next to that cell to aggregate — select the range first, or type the arguments in.",
  });
}

/* ------------------------------------------------------------------ */
/* PDF upload                                                          */
/* ------------------------------------------------------------------ */

/**
 * Confirms a local add. Deliberately never says "uploaded to server": the
 * bytes go to this browser's IndexedDB and nowhere else.
 */
export function notifyPdfAdded(count: number): void {
  toast.success(
    count === 1
      ? "PDF added to this local workspace."
      : `${count} PDFs added to this local workspace.`,
    { description: "Stored locally in this browser — nothing was uploaded." },
  );
}

export function notifyPdfRejected(rejected: readonly RejectedPdfFile[]): void {
  if (rejected.length === 0) return;
  const [first] = rejected;
  toast.error(
    rejected.length === 1
      ? `“${first.name}” was not added — ${first.reason}.`
      : `${rejected.length} files were not added.`,
    rejected.length > 1
      ? {
          description: rejected
            .map((file) => `${file.name} — ${file.reason}`)
            .join("\n"),
        }
      : undefined,
  );
}

export function notifyPdfBatchTruncated(): void {
  toast.warning(
    `Up to ${MAX_PDF_UPLOAD_BATCH} PDFs can be added at a time.`,
    { description: "The extra files were skipped — add them in another go." },
  );
}

export function notifyPdfStoreFailed(reason: string): void {
  toast.error("PDF could not be stored locally.", { description: reason });
}
