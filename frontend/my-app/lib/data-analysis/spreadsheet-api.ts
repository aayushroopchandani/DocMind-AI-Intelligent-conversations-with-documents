import type {
  ImportedWorkbookResponse,
  WorkbookDocument,
} from "@/lib/data-analysis/sheet/workbook-document";

/**
 * Client for the spreadsheet import/export endpoints.
 *
 * Both go through the Next route handlers, which verify the Clerk session
 * before forwarding to FastAPI — the browser never talks to the backend
 * directly, matching the rest of the analysis API surface.
 */

async function errorMessage(response: Response): Promise<string> {
  const payload = (await response.json().catch(() => null)) as
    | { detail?: string | { message?: string }; error?: string }
    | null;
  if (typeof payload?.detail === "string") return payload.detail;
  if (payload?.detail && typeof payload.detail === "object") {
    return payload.detail.message ?? "The spreadsheet could not be converted.";
  }
  return (
    payload?.error ??
    response.statusText ??
    "The spreadsheet could not be converted."
  );
}

/** Upload an `.xlsx` or `.csv` and get the converted workbook back. */
export async function importSpreadsheetFile(
  file: File,
): Promise<ImportedWorkbookResponse> {
  const body = new FormData();
  body.append("file", file);

  const response = await fetch("/api/analysis/spreadsheets/import", {
    method: "POST",
    body,
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return (await response.json()) as ImportedWorkbookResponse;
}

/** Render a workbook as XLSX bytes. */
export async function exportSpreadsheetDocument(
  document: WorkbookDocument,
  fileName: string,
): Promise<Blob> {
  const response = await fetch("/api/analysis/spreadsheets/export", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ filename: fileName, document }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.blob();
}
