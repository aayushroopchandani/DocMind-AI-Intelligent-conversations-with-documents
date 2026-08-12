import { notifyFormulaInserted } from "@/lib/data-analysis/feedback";
import { insertFunction } from "@/lib/data-analysis/sheet/formula-commands";

/**
 * Actions shared by more than one menu.
 *
 * Commands in `lib/data-analysis/sheet/*` stay silent — they touch the
 * workbook and nothing else — so the user-facing feedback that goes with
 * them lives here, next to the menus that trigger it.
 */

/** Writes a function over the selection and reports where it landed. */
export function applyFunction(name: string): void {
  const result = insertFunction(name);
  if (!result) return;
  notifyFormulaInserted(name, result.cell, result.reference);
}
