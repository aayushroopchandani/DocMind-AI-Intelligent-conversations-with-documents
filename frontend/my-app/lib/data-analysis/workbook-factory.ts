import type { IWorkbookData } from "@univerjs/core";

/**
 * Builds the snapshot for a brand-new blank workbook.
 *
 * The workbook id is the artifact id, so the Univer unit id, workspace tab
 * and localStorage key all share one identifier.
 */
export function createBlankWorkbookData(
  artifactId: string,
  name: string,
): Partial<IWorkbookData> {
  const sheetId = `${artifactId}-sheet-1`;
  return {
    id: artifactId,
    name,
    sheetOrder: [sheetId],
    sheets: {
      [sheetId]: {
        id: sheetId,
        name: "Sheet1",
        rowCount: 1000,
        columnCount: 26,
      },
    },
  };
}

/**
 * Clones a snapshot for "Duplicate": same content, new identity.
 * The copy keeps worksheet ids — only the workbook id must be unique.
 */
export function cloneWorkbookData(
  source: Partial<IWorkbookData>,
  newArtifactId: string,
  newName: string,
): Partial<IWorkbookData> {
  const clone = structuredClone(source);
  clone.id = newArtifactId;
  clone.name = newName;
  return clone;
}
