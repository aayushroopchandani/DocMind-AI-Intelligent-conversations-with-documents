import { toast } from "sonner";
import { MAX_PDF_UPLOAD_BATCH } from "@/lib/data-analysis/constants";
import type { RejectedPdfFile } from "@/lib/data-analysis/pdf/pdf-types";

/**
 * Honest "not wired up yet" feedback for controls that must be visible now
 * but only gain behaviour once the data-analysis backend ships.
 */
export const PENDING_FEATURE_MESSAGES = {
  import:
    "XLSX, XLS and CSV import will be connected through the backend in a later milestone.",
  export: "Spreadsheet export will be connected to the backend in a later milestone.",
  share: "Sharing will be available once the backend integration lands.",
  dataSource: "Data sources will be connected to the backend in a later milestone.",
  analyst: "The data-analysis backend is still being connected.",
} as const;

export type PendingFeature = keyof typeof PENDING_FEATURE_MESSAGES;

export function notifyPendingFeature(feature: PendingFeature): void {
  toast.info(PENDING_FEATURE_MESSAGES[feature]);
}

export function notifyStorageFull(): void {
  toast.error(
    "Local draft could not be saved — this browser's storage is full.",
  );
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
