import type { LucideIcon } from "lucide-react";
import type {
  WorkspaceActions,
  WorkspaceUiState,
} from "@/components/data-analysis/workspace-provider";
import type { ArtifactMeta, LayoutState } from "@/lib/data-analysis/types";

/**
 * Declarative model behind the spreadsheet menu bar.
 *
 * Menus are described as data, not JSX: one renderer walks the tree, so a
 * new entry is a single object literal and every menu inherits the same
 * keyboard behaviour, disabled styling and "backend pending" treatment.
 */

interface NodeBase {
  /** Unique within its own menu — used as the React key. */
  id: string;
}

export interface MenuItemNode extends NodeBase {
  kind: "item";
  label: string;
  icon?: LucideIcon;
  /** Right-aligned hint: a keyboard shortcut, or a short descriptor. */
  shortcut?: string;
  disabled?: boolean;
  destructive?: boolean;
  /** Marks an action the analysis backend has to land before it works. */
  pending?: boolean;
  onSelect: () => void;
}

export interface MenuCheckboxNode extends NodeBase {
  kind: "checkbox";
  label: string;
  icon?: LucideIcon;
  shortcut?: string;
  checked: boolean;
  disabled?: boolean;
  onSelect: () => void;
}

export interface MenuSubmenuNode extends NodeBase {
  kind: "submenu";
  label: string;
  icon?: LucideIcon;
  disabled?: boolean;
  items: MenuNode[];
}

/** A colour grid — text and fill colour need swatches, not a list. */
export interface MenuSwatchNode extends NodeBase {
  kind: "swatch";
  /** Rendered above the grid, e.g. "Text colour". */
  label: string;
  /** Offers a "default"/"none" reset tile above the grid. */
  resetLabel?: string;
  onSelect: (color: string | null) => void;
}

export interface MenuLabelNode extends NodeBase {
  kind: "label";
  label: string;
}

export interface MenuNoteNode extends NodeBase {
  kind: "note";
  text: string;
}

export interface MenuSeparatorNode extends NodeBase {
  kind: "separator";
}

export type MenuNode =
  | MenuItemNode
  | MenuCheckboxNode
  | MenuSubmenuNode
  | MenuSwatchNode
  | MenuLabelNode
  | MenuNoteNode
  | MenuSeparatorNode;

/**
 * Everything a menu needs to describe itself.
 *
 * Live spreadsheet state (bold, gridlines, zoom…) is *not* in here: menus
 * read that straight off the Univer facade when they build, which only
 * happens as the menu opens.
 */
export interface MenuContext {
  /** A spreadsheet is in front and its engine is live. */
  sheetReady: boolean;
  /** The workspace already holds a workbook. */
  hasWorkbook: boolean;
  activeArtifact: ArtifactMeta | undefined;
  /** Display name of the workbook, used for export file names. */
  workbookName: string;
  layout: LayoutState;
  updateLayout: (patch: Partial<LayoutState>) => void;
  actions: WorkspaceActions;
  ui: WorkspaceUiState;
  /** Opens the menu bar's hidden PDF file input. */
  openPdfPicker: () => void;
  /** Opens the menu bar's hidden spreadsheet import input. */
  openSpreadsheetPicker: () => void;
  /** Flushes the open workbook's snapshot to local storage right now. */
  saveNow: () => void;
  /** Moves focus to the project-name field in the app bar. */
  focusProjectName: () => void;
}

export interface MenuDefinition {
  id: string;
  label: string;
  /** Built as the menu opens, so nothing it reports is ever stale. */
  build: (context: MenuContext) => MenuNode[];
}
