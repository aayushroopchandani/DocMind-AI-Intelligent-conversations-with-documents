"use client";

import { Menubar } from "@base-ui/react/menubar";
import { Menu as MenuIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MENU_BAR } from "@/lib/data-analysis/menus/definitions";
import type {
  MenuContext,
  MenuDefinition,
} from "@/lib/data-analysis/menus/menu-types";
import { MenuNodeList } from "@/components/data-analysis/menubar/menu-node-list";

/**
 * Application menu bar: File · Edit · View · Insert · Format · Formulas.
 *
 * Base UI's `Menubar` supplies the desktop behaviour the workspace needs —
 * once one menu is open, moving across the others switches to them, and
 * arrow keys walk the whole bar. `modal={false}` keeps the grid live
 * underneath instead of locking the page while a menu is open.
 */
export function MenuBar({ context }: { context: MenuContext }) {
  return (
    <Menubar
      modal={false}
      aria-label="Spreadsheet menus"
      className="hidden items-center gap-px md:flex"
    >
      {MENU_BAR.map((definition) => (
        <DropdownMenu key={definition.id}>
          <DropdownMenuTrigger
            render={
              <button
                type="button"
                className="h-7 shrink-0 rounded-md px-2 text-[13px] text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 data-popup-open:bg-muted data-popup-open:text-foreground"
              >
                {definition.label}
              </button>
            }
          />
          <DropdownMenuContent align="start" sideOffset={6} className="w-64">
            <MenuContents definition={definition} context={context} />
          </DropdownMenuContent>
        </DropdownMenu>
      ))}
    </Menubar>
  );
}

/**
 * Narrow-viewport form: the same six menus collapse into one trigger with a
 * submenu each, so nothing is lost below the `md` breakpoint.
 */
export function CompactMenuBar({ context }: { context: MenuContext }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Spreadsheet menus"
            className="md:hidden"
          >
            <MenuIcon />
          </Button>
        }
      />
      <DropdownMenuContent align="start" sideOffset={6} className="w-48">
        {MENU_BAR.map((definition) => (
          <DropdownMenuSub key={definition.id}>
            <DropdownMenuSubTrigger>{definition.label}</DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="w-64">
              <MenuContents definition={definition} context={context} />
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * Builds one menu's nodes.
 *
 * A component rather than an inline call, because Base UI only mounts a
 * popup's children once it opens: the facade probes behind the checkmarks
 * (bold, gridlines, zoom, freeze) then run at open time instead of on every
 * app-bar render.
 */
function MenuContents({
  definition,
  context,
}: {
  definition: MenuDefinition;
  context: MenuContext;
}) {
  return <MenuNodeList nodes={definition.build(context)} />;
}
