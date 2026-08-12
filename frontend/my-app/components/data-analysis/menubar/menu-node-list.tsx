"use client";

import { useState } from "react";
import {
  DropdownMenuCheckboxItem,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";
import type {
  MenuCheckboxNode,
  MenuNode,
} from "@/lib/data-analysis/menus/menu-types";
import { ColorSwatchGrid } from "@/components/data-analysis/menubar/color-swatch-grid";

/**
 * The single renderer for every menu in the bar.
 *
 * Menus are declared as data (`lib/data-analysis/menus`), so keyboard
 * behaviour, disabled styling, submenu nesting and the "backend pending"
 * badge are implemented once here instead of per menu.
 */

interface Section {
  id: string;
  label?: string;
  nodes: MenuNode[];
}

/**
 * Splits a flat node list into labelled sections.
 *
 * Base UI requires a `GroupLabel` to live inside a `Group`, so each label
 * opens a new group that runs until the next one — which is also the
 * correct accessible grouping.
 */
function toSections(nodes: readonly MenuNode[]): Section[] {
  const sections: Section[] = [];
  let current: Section = { id: "__root", nodes: [] };

  for (const node of nodes) {
    if (node.kind === "label") {
      if (current.nodes.length > 0) sections.push(current);
      current = { id: node.id, label: node.label, nodes: [] };
      continue;
    }
    current.nodes.push(node);
  }
  if (current.nodes.length > 0) sections.push(current);
  return sections;
}

export function MenuNodeList({ nodes }: { nodes: readonly MenuNode[] }) {
  return (
    <>
      {toSections(nodes).map((section) =>
        section.label ? (
          <DropdownMenuGroup key={section.id}>
            <DropdownMenuLabel>{section.label}</DropdownMenuLabel>
            {section.nodes.map((node) => (
              <MenuNodeView key={node.id} node={node} />
            ))}
          </DropdownMenuGroup>
        ) : (
          section.nodes.map((node) => <MenuNodeView key={node.id} node={node} />)
        ),
      )}
    </>
  );
}

function MenuNodeView({ node }: { node: MenuNode }) {
  switch (node.kind) {
    case "item": {
      const Icon = node.icon;
      return (
        <DropdownMenuItem
          disabled={node.disabled}
          variant={node.destructive ? "destructive" : "default"}
          onClick={node.onSelect}
        >
          {Icon ? <Icon /> : null}
          <span className="truncate">{node.label}</span>
          {node.pending ? (
            <PendingBadge />
          ) : node.shortcut ? (
            <DropdownMenuShortcut>{node.shortcut}</DropdownMenuShortcut>
          ) : null}
        </DropdownMenuItem>
      );
    }

    case "checkbox":
      return <CheckboxNodeView node={node} />;

    case "submenu": {
      const Icon = node.icon;
      return (
        <DropdownMenuSub>
          <DropdownMenuSubTrigger disabled={node.disabled}>
            {Icon ? <Icon /> : null}
            <span className="truncate">{node.label}</span>
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="w-56">
            <MenuNodeList nodes={node.items} />
          </DropdownMenuSubContent>
        </DropdownMenuSub>
      );
    }

    case "swatch":
      return (
        <ColorSwatchGrid
          label={node.label}
          resetLabel={node.resetLabel}
          onSelect={node.onSelect}
        />
      );

    case "note":
      return (
        <p className="px-2 pt-1.5 pb-1 text-[11px] leading-relaxed text-muted-foreground/70">
          {node.text}
        </p>
      );

    case "separator":
      return <DropdownMenuSeparator />;

    // Labels are consumed by `toSections` and never reach this renderer.
    case "label":
      return null;
  }
}

/**
 * Toggle entries (gridlines, wrap text, panels…) read their initial value
 * from live state when the menu opens, then own it for as long as the menu
 * stays open.
 *
 * Base UI drives `CheckboxItem` through `onCheckedChange`, and these menus
 * do not close on a toggle — so without local state the tick would sit at
 * its build-time value while the workbook had already moved on.
 */
function CheckboxNodeView({ node }: { node: MenuCheckboxNode }) {
  const [checked, setChecked] = useState(node.checked);
  const Icon = node.icon;

  return (
    <DropdownMenuCheckboxItem
      checked={checked}
      disabled={node.disabled}
      onCheckedChange={(next: boolean) => {
        setChecked(next);
        node.onSelect();
      }}
    >
      {Icon ? <Icon /> : null}
      <span className="truncate">{node.label}</span>
      {node.shortcut ? (
        // Reserve the indicator column so the tick never overlaps.
        <DropdownMenuShortcut className="mr-4">
          {node.shortcut}
        </DropdownMenuShortcut>
      ) : null}
    </DropdownMenuCheckboxItem>
  );
}

function PendingBadge() {
  return (
    <span className="ml-auto rounded border border-border px-1 py-px text-[10px] font-medium uppercase tracking-wide text-muted-foreground/80">
      Soon
    </span>
  );
}
