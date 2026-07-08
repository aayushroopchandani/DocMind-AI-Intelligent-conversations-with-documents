/** Shared PDF validation + formatting helpers (client-side). */

/** Maximum accepted PDF size (bytes). */
export const MAX_SIZE_BYTES = 25 * 1024 * 1024; // 25 MB

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export interface ValidationResult {
  accepted: File[];
  /** Human-readable summary of anything rejected/capped, or null if all good. */
  message: string | null;
}

/**
 * Validate a batch of selected files against the PDF + size rules and the
 * per-chat limit. Never silently drops files — anything skipped is reported.
 */
export function validatePdfFiles(
  files: File[],
  remaining: number,
  maxFiles: number,
): ValidationResult {
  const accepted: File[] = [];
  const messages: string[] = [];

  for (const file of files) {
    const isPdf =
      file.type === "application/pdf" ||
      file.name.toLowerCase().endsWith(".pdf");
    if (!isPdf) {
      messages.push(`${file.name} isn't a PDF`);
      continue;
    }
    if (file.size > MAX_SIZE_BYTES) {
      messages.push(`${file.name} is larger than ${formatBytes(MAX_SIZE_BYTES)}`);
      continue;
    }
    accepted.push(file);
  }

  let toAdd = accepted;
  if (accepted.length > remaining) {
    toAdd = accepted.slice(0, Math.max(0, remaining));
    messages.push(
      `Only ${remaining} more PDF${remaining === 1 ? "" : "s"} can be added (max ${maxFiles} per chat).`,
    );
  }

  return { accepted: toAdd, message: messages.length ? messages.join(" · ") : null };
}
