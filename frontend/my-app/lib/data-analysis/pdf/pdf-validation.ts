import {
  MAX_PDF_FILE_BYTES,
  MAX_PDF_UPLOAD_BATCH,
  PDF_MIME_TYPE,
} from "@/lib/data-analysis/constants";
import type {
  PdfValidationResult,
  RejectedPdfFile,
} from "@/lib/data-analysis/pdf/pdf-types";

/**
 * Front-door validation for user-selected files.
 *
 * Extension *and* MIME type are checked because browsers disagree: some
 * report an empty `type` for drag-and-drop, and some report
 * `application/octet-stream` for files copied off a network share. A `.pdf`
 * extension with a blank type is accepted — the engine is the real arbiter
 * of whether the bytes parse, and it reports malformed files as an error
 * state inside the workspace.
 */
function hasPdfExtension(name: string): boolean {
  return name.toLowerCase().endsWith(".pdf");
}

function looksLikePdf(file: File): boolean {
  if (file.type === PDF_MIME_TYPE) return true;
  // Blank / generic types are common; fall back to the extension.
  return (
    hasPdfExtension(file.name) &&
    (file.type === "" || file.type === "application/octet-stream")
  );
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Splits a selection into files worth storing and files to report back.
 *
 * At most `MAX_PDF_UPLOAD_BATCH` files are accepted per operation; the rest
 * are reported via `truncated` so the caller can explain the cap instead of
 * silently dropping them.
 */
export function validatePdfSelection(
  files: readonly File[],
): PdfValidationResult {
  const accepted: File[] = [];
  const rejected: RejectedPdfFile[] = [];
  let truncated = false;

  for (const file of files) {
    if (!looksLikePdf(file)) {
      rejected.push({ name: file.name, reason: "not a PDF file" });
      continue;
    }
    if (file.size === 0) {
      rejected.push({ name: file.name, reason: "the file is empty" });
      continue;
    }
    if (file.size > MAX_PDF_FILE_BYTES) {
      rejected.push({
        name: file.name,
        reason: `larger than ${formatFileSize(MAX_PDF_FILE_BYTES)}`,
      });
      continue;
    }
    if (accepted.length >= MAX_PDF_UPLOAD_BATCH) {
      truncated = true;
      continue;
    }
    accepted.push(file);
  }

  return { accepted, rejected, truncated };
}

/** True when a drag event carries at least one file. */
export function dragEventHasFiles(dataTransfer: DataTransfer | null): boolean {
  if (!dataTransfer) return false;
  return Array.from(dataTransfer.types).includes("Files");
}

/**
 * Makes a display name unique against names already in the workspace.
 *
 * Uploading the same file twice is legitimate, so duplicates are numbered
 * rather than overwritten — each upload gets its own artifact id regardless.
 */
export function uniqueArtifactName(
  desired: string,
  takenNames: ReadonlySet<string>,
): string {
  if (!takenNames.has(desired)) return desired;

  const dot = desired.lastIndexOf(".");
  const stem = dot > 0 ? desired.slice(0, dot) : desired;
  const extension = dot > 0 ? desired.slice(dot) : "";

  for (let counter = 2; ; counter += 1) {
    const candidate = `${stem} (${counter})${extension}`;
    if (!takenNames.has(candidate)) return candidate;
  }
}
