import { FunctionSquare, Sigma } from "lucide-react";
import { applyFunction } from "@/lib/data-analysis/menus/menu-actions";
import type { MenuDefinition } from "@/lib/data-analysis/menus/menu-types";
import {
  AUTOSUM_FUNCTIONS,
  FUNCTION_GROUPS,
} from "@/lib/data-analysis/sheet/formula-commands";

/**
 * Formulas menu — the fast path into the formula engine.
 *
 * Univer's Formulas ribbon stays exactly as it is and remains the full
 * reference (every function, argument hints, the fx browser). This menu adds
 * the part a ribbon is bad at: one click writes a *complete* formula against
 * the current selection, picking the argument range the way AutoSum does.
 */
export const formulasMenu: MenuDefinition = {
  id: "formulas",
  label: "Formulas",
  build: (context) => {
    const disabled = !context.sheetReady;

    return [
      { kind: "label", id: "autosum-label", label: "AutoSum" },
      ...AUTOSUM_FUNCTIONS.map((name) => ({
        kind: "item" as const,
        id: `autosum-${name}`,
        label: name,
        icon: name === "SUM" ? Sigma : undefined,
        disabled,
        onSelect: () => applyFunction(name),
      })),
      { kind: "separator", id: "sep-library" },

      { kind: "label", id: "library-label", label: "Function library" },
      ...FUNCTION_GROUPS.map((group) => ({
        kind: "submenu" as const,
        id: `group-${group.id}`,
        label: group.label,
        icon: FunctionSquare,
        disabled,
        items: group.functions.map((fn) => ({
          kind: "item" as const,
          id: `fn-${fn.name}`,
          label: fn.name,
          shortcut: fn.hint,
          onSelect: () => applyFunction(fn.name),
        })),
      })),
      {
        kind: "note",
        id: "note",
        text: "Each entry writes a complete formula over your selection. The Formulas ribbon below has the full library.",
      },
    ];
  },
};
