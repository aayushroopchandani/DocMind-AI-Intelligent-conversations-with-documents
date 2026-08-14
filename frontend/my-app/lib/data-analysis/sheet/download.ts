/**
 * Handing a generated file to the browser.
 *
 * Shared by CSV export (built in the page) and XLSX export (fetched from the
 * backend) so the object-URL lifetime is handled the same way in both.
 */

/** Strips characters browsers or file systems reject in a download name. */
export function safeFileName(name: string, fallback = "workbook"): string {
  const cleaned = name.replace(/[^\w\s.-]+/g, " ").trim();
  return (cleaned || fallback).slice(0, 80);
}

export function downloadBlob(fileName: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  // Revoke on the next frame: Safari aborts the download if the object URL
  // disappears in the same tick as the click.
  requestAnimationFrame(() => URL.revokeObjectURL(url));
}
