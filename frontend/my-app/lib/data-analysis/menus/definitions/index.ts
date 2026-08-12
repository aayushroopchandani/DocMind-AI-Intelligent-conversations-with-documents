import type { MenuDefinition } from "@/lib/data-analysis/menus/menu-types";
import { editMenu } from "@/lib/data-analysis/menus/definitions/edit-menu";
import { fileMenu } from "@/lib/data-analysis/menus/definitions/file-menu";
import { formatMenu } from "@/lib/data-analysis/menus/definitions/format-menu";
import { formulasMenu } from "@/lib/data-analysis/menus/definitions/formulas-menu";
import { insertMenu } from "@/lib/data-analysis/menus/definitions/insert-menu";
import { viewMenu } from "@/lib/data-analysis/menus/definitions/view-menu";

/**
 * The menu bar, left to right.
 *
 * No Help and no Feedback: support lives on the marketing site, and a
 * feedback entry inside the workspace would be noise. Formulas stays because
 * it is the one menu that carries this product's own value.
 */
export const MENU_BAR: readonly MenuDefinition[] = [
  fileMenu,
  editMenu,
  viewMenu,
  insertMenu,
  formatMenu,
  formulasMenu,
];
