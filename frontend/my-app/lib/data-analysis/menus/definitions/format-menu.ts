import {
  AlignLeft,
  Bold,
  Eraser,
  Grid2x2,
  Hash,
  Italic,
  Merge,
  Strikethrough,
  Type,
  Underline,
  WrapText,
} from "lucide-react";
import type {
  MenuDefinition,
  MenuNode,
} from "@/lib/data-analysis/menus/menu-types";
import {
  clearFormatting,
  isBold,
  isItalic,
  isMerged,
  isStruckThrough,
  isUnderlined,
  isWrapped,
  mergeAcross,
  mergeSelection,
  setBackgroundColor,
  setBorder,
  setFontColor,
  setFontFamily,
  setFontSize,
  setHorizontalAlignment,
  setNumberFormat,
  setVerticalAlignment,
  toggleBold,
  toggleItalic,
  toggleStrikethrough,
  toggleUnderline,
  toggleWrap,
  unmergeSelection,
  type BorderPreset,
  type NumberFormatKey,
} from "@/lib/data-analysis/sheet/format-commands";

/** Sizes offered in the menu; the ribbon's own input covers the rest. */
const FONT_SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 24, 30, 36] as const;

/** Web-safe families plus the app's own stack, so exports stay portable. */
const FONT_FAMILIES = [
  "Arial",
  "Helvetica",
  "Times New Roman",
  "Georgia",
  "Verdana",
  "Courier New",
  "Menlo",
] as const;

const NUMBER_FORMAT_ENTRIES: readonly {
  key: NumberFormatKey;
  label: string;
  sample: string;
}[] = [
  { key: "automatic", label: "Automatic", sample: "1234.57" },
  { key: "plainText", label: "Plain text", sample: "1234.57" },
  { key: "integer", label: "Number", sample: "1,235" },
  { key: "number", label: "Number (2 dp)", sample: "1,234.57" },
  { key: "percent", label: "Percent", sample: "12.35%" },
  { key: "currency", label: "Currency", sample: "$1,234.57" },
  { key: "accounting", label: "Accounting", sample: "$ 1,234.57" },
  { key: "scientific", label: "Scientific", sample: "1.23E+03" },
  { key: "date", label: "Date", sample: "2026-08-12" },
  { key: "time", label: "Time", sample: "14:30:00" },
  { key: "dateTime", label: "Date and time", sample: "2026-08-12 14:30" },
  { key: "duration", label: "Duration", sample: "24:30:00" },
];

const BORDER_ENTRIES: readonly { preset: BorderPreset; label: string }[] = [
  { preset: "all", label: "All borders" },
  { preset: "outside", label: "Outer border" },
  { preset: "inside", label: "Inner borders" },
  { preset: "top", label: "Top" },
  { preset: "bottom", label: "Bottom" },
  { preset: "left", label: "Left" },
  { preset: "right", label: "Right" },
  { preset: "none", label: "No border" },
];

/**
 * Format menu — every entry is a Univer facade call, so the whole menu works
 * offline and needs nothing from the backend.
 */
export const formatMenu: MenuDefinition = {
  id: "format",
  label: "Format",
  build: (context): MenuNode[] => {
    const disabled = !context.sheetReady;
    // Style probes touch the facade, so they only run when it is live.
    const live = context.sheetReady;

    return [
      {
        kind: "checkbox",
        id: "bold",
        label: "Bold",
        icon: Bold,
        shortcut: "⌘B",
        checked: live && isBold(),
        disabled,
        onSelect: toggleBold,
      },
      {
        kind: "checkbox",
        id: "italic",
        label: "Italic",
        icon: Italic,
        shortcut: "⌘I",
        checked: live && isItalic(),
        disabled,
        onSelect: toggleItalic,
      },
      {
        kind: "checkbox",
        id: "underline",
        label: "Underline",
        icon: Underline,
        shortcut: "⌘U",
        checked: live && isUnderlined(),
        disabled,
        onSelect: toggleUnderline,
      },
      {
        kind: "checkbox",
        id: "strikethrough",
        label: "Strikethrough",
        icon: Strikethrough,
        checked: live && isStruckThrough(),
        disabled,
        onSelect: toggleStrikethrough,
      },
      { kind: "separator", id: "sep-font" },

      {
        kind: "submenu",
        id: "font-size",
        label: "Font size",
        icon: Type,
        disabled,
        items: FONT_SIZES.map((size) => ({
          kind: "item" as const,
          id: `size-${size}`,
          label: String(size),
          onSelect: () => setFontSize(size),
        })),
      },
      {
        kind: "submenu",
        id: "font-family",
        label: "Font",
        disabled,
        items: FONT_FAMILIES.map((family) => ({
          kind: "item" as const,
          id: `family-${family}`,
          label: family,
          onSelect: () => setFontFamily(family),
        })),
      },
      {
        kind: "submenu",
        id: "text-colour",
        label: "Text colour",
        disabled,
        items: [
          {
            kind: "swatch",
            id: "text-swatch",
            label: "Text colour",
            resetLabel: "Default",
            onSelect: (color) => setFontColor(color),
          },
        ],
      },
      {
        kind: "submenu",
        id: "fill-colour",
        label: "Fill colour",
        disabled,
        items: [
          {
            kind: "swatch",
            id: "fill-swatch",
            label: "Fill colour",
            resetLabel: "No fill",
            onSelect: (color) => setBackgroundColor(color ?? "transparent"),
          },
        ],
      },
      { kind: "separator", id: "sep-align" },

      {
        kind: "submenu",
        id: "alignment",
        label: "Alignment",
        icon: AlignLeft,
        disabled,
        items: [
          { kind: "label", id: "h-label", label: "Horizontal" },
          {
            kind: "item",
            id: "align-left",
            label: "Left",
            onSelect: () => setHorizontalAlignment("left"),
          },
          {
            kind: "item",
            id: "align-center",
            label: "Centre",
            onSelect: () => setHorizontalAlignment("center"),
          },
          {
            kind: "item",
            id: "align-right",
            label: "Right",
            onSelect: () => setHorizontalAlignment("normal"),
          },
          { kind: "separator", id: "sep-vertical" },
          { kind: "label", id: "v-label", label: "Vertical" },
          {
            kind: "item",
            id: "align-top",
            label: "Top",
            onSelect: () => setVerticalAlignment("top"),
          },
          {
            kind: "item",
            id: "align-middle",
            label: "Middle",
            onSelect: () => setVerticalAlignment("middle"),
          },
          {
            kind: "item",
            id: "align-bottom",
            label: "Bottom",
            onSelect: () => setVerticalAlignment("bottom"),
          },
        ],
      },
      {
        kind: "checkbox",
        id: "wrap",
        label: "Wrap text",
        icon: WrapText,
        checked: live && isWrapped(),
        disabled,
        onSelect: toggleWrap,
      },
      {
        kind: "submenu",
        id: "merge",
        label: "Merge cells",
        icon: Merge,
        disabled,
        items: [
          {
            kind: "item",
            id: "merge-all",
            label: "Merge all",
            onSelect: mergeSelection,
          },
          {
            kind: "item",
            id: "merge-across",
            label: "Merge across rows",
            onSelect: mergeAcross,
          },
          {
            kind: "item",
            id: "unmerge",
            label: "Unmerge",
            disabled: live && !isMerged(),
            onSelect: unmergeSelection,
          },
        ],
      },
      { kind: "separator", id: "sep-number" },

      {
        kind: "submenu",
        id: "number",
        label: "Number",
        icon: Hash,
        disabled,
        items: NUMBER_FORMAT_ENTRIES.map((entry) => ({
          kind: "item" as const,
          id: `number-${entry.key}`,
          label: entry.label,
          shortcut: entry.sample,
          onSelect: () => setNumberFormat(entry.key),
        })),
      },
      {
        kind: "submenu",
        id: "borders",
        label: "Borders",
        icon: Grid2x2,
        disabled,
        items: BORDER_ENTRIES.map((entry) => ({
          kind: "item" as const,
          id: `border-${entry.preset}`,
          label: entry.label,
          onSelect: () => setBorder(entry.preset),
        })),
      },
      { kind: "separator", id: "sep-clear" },

      {
        kind: "item",
        id: "clear-format",
        label: "Clear formatting",
        icon: Eraser,
        disabled,
        onSelect: clearFormatting,
      },
    ];
  },
};
