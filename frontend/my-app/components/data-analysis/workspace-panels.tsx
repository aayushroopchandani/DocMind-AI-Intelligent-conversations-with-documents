"use client";

import {
  useCallback,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { FilePlus2, PanelLeftOpen, PanelRightOpen, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  CENTER_MIN_WIDTH,
  LEFT_PANEL_COLLAPSE_AT,
  LEFT_PANEL_MAX,
  LEFT_PANEL_MIN,
  LEFT_PANEL_DEFAULT,
  PANEL_RAIL_WIDTH,
  RIGHT_PANEL_COLLAPSE_AT,
  RIGHT_PANEL_MAX,
  RIGHT_PANEL_MIN,
  RIGHT_PANEL_DEFAULT,
} from "@/lib/data-analysis/constants";
import { useIsDesktop } from "@/lib/data-analysis/use-is-desktop";
import { AiAnalystPanel } from "@/components/data-analysis/analyst/ai-analyst-panel";
import { FileExplorer } from "@/components/data-analysis/explorer/file-explorer";
import { WorkspaceShell } from "@/components/data-analysis/workspace/workspace-shell";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";
import { cn } from "@/lib/utils";

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/**
 * Three-column IDE layout.
 *
 * Desktop (≥1024px): explorer | workspace | analyst with draggable handles;
 * either side collapses into a 48px icon rail. Below that, the workspace
 * runs full-width and the side panels become overlay sheets so nothing gets
 * crushed. Sizes and collapsed state persist via the workspace provider.
 */
export function WorkspacePanels() {
  const { layout, updateLayout, ui } = useWorkspace();
  const isDesktop = useIsDesktop();
  const containerRef = useRef<HTMLDivElement>(null);

  const clampLeft = useCallback(
    (size: number) => {
      const width = containerRef.current?.clientWidth ?? Infinity;
      const rightWidth = layout.rightCollapsed
        ? PANEL_RAIL_WIDTH
        : layout.rightSize;
      const max = Math.min(
        LEFT_PANEL_MAX,
        width - rightWidth - CENTER_MIN_WIDTH,
      );
      return clamp(size, LEFT_PANEL_MIN, Math.max(LEFT_PANEL_MIN, max));
    },
    [layout.rightCollapsed, layout.rightSize],
  );

  const clampRight = useCallback(
    (size: number) => {
      const width = containerRef.current?.clientWidth ?? Infinity;
      const leftWidth = layout.leftCollapsed
        ? PANEL_RAIL_WIDTH
        : layout.leftSize;
      const max = Math.min(
        RIGHT_PANEL_MAX,
        width - leftWidth - CENTER_MIN_WIDTH,
      );
      return clamp(size, RIGHT_PANEL_MIN, Math.max(RIGHT_PANEL_MIN, max));
    },
    [layout.leftCollapsed, layout.leftSize],
  );

  const resizeLeft = useCallback(
    (size: number) => {
      if (size <= LEFT_PANEL_COLLAPSE_AT) {
        updateLayout({ leftCollapsed: true });
      } else {
        updateLayout({ leftCollapsed: false, leftSize: clampLeft(size) });
      }
    },
    [clampLeft, updateLayout],
  );

  const resizeRight = useCallback(
    (size: number) => {
      if (size <= RIGHT_PANEL_COLLAPSE_AT) {
        updateLayout({ rightCollapsed: true });
      } else {
        updateLayout({ rightCollapsed: false, rightSize: clampRight(size) });
      }
    },
    [clampRight, updateLayout],
  );

  if (!isDesktop) {
    return (
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <WorkspaceShell />

        <Sheet open={ui.explorerSheetOpen} onOpenChange={ui.setExplorerSheetOpen}>
          <SheetContent side="left" className="w-80 gap-0 p-0">
            <SheetHeader className="sr-only">
              <SheetTitle>File explorer</SheetTitle>
            </SheetHeader>
            <FileExplorer />
          </SheetContent>
        </Sheet>

        <Sheet open={ui.analystSheetOpen} onOpenChange={ui.setAnalystSheetOpen}>
          <SheetContent side="right" className="w-[min(24rem,90vw)] gap-0 p-0">
            <SheetHeader className="sr-only">
              <SheetTitle>AI analyst</SheetTitle>
            </SheetHeader>
            <AiAnalystPanel />
          </SheetContent>
        </Sheet>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="flex min-h-0 min-w-0 flex-1">
      <aside
        key="left"
        aria-label="File explorer panel"
        style={{
          width: layout.leftCollapsed ? PANEL_RAIL_WIDTH : layout.leftSize,
        }}
        className="shrink-0 overflow-hidden border-r border-border bg-card/30"
      >
        {layout.leftCollapsed ? (
          <ExplorerRail />
        ) : (
          <FileExplorer
            onCollapse={() => updateLayout({ leftCollapsed: true })}
          />
        )}
      </aside>

      {!layout.leftCollapsed ? (
        <PanelResizeHandle
          key="left-handle"
          label="Resize file explorer"
          currentSize={layout.leftSize}
          onResize={resizeLeft}
          onReset={() =>
            updateLayout({ leftCollapsed: false, leftSize: LEFT_PANEL_DEFAULT })
          }
        />
      ) : null}

      <main key="center" className="min-h-0 min-w-0 flex-1">
        <WorkspaceShell />
      </main>

      {!layout.rightCollapsed ? (
        <PanelResizeHandle
          key="right-handle"
          label="Resize AI analyst"
          inverted
          currentSize={layout.rightSize}
          onResize={resizeRight}
          onReset={() =>
            updateLayout({
              rightCollapsed: false,
              rightSize: RIGHT_PANEL_DEFAULT,
            })
          }
        />
      ) : null}

      <aside
        key="right"
        aria-label="AI analyst panel"
        style={{
          width: layout.rightCollapsed ? PANEL_RAIL_WIDTH : layout.rightSize,
        }}
        className="shrink-0 overflow-hidden border-l border-border bg-card/30"
      >
        {layout.rightCollapsed ? (
          <AnalystRail />
        ) : (
          <AiAnalystPanel
            onCollapse={() => updateLayout({ rightCollapsed: true })}
          />
        )}
      </aside>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Collapsed icon rails                                                */
/* ------------------------------------------------------------------ */

function ExplorerRail() {
  const { actions, updateLayout } = useRailHelpers();

  return (
    <div className="flex h-full flex-col items-center gap-1 py-2 animate-in fade-in duration-200">
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Expand file explorer"
              onClick={() => updateLayout({ leftCollapsed: false })}
            >
              <PanelLeftOpen />
            </Button>
          }
        />
        <TooltipContent side="right">Expand files</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="New blank spreadsheet"
              onClick={actions.createSpreadsheet}
            >
              <FilePlus2 />
            </Button>
          }
        />
        <TooltipContent side="right">New blank spreadsheet</TooltipContent>
      </Tooltip>
    </div>
  );
}

function AnalystRail() {
  const { updateLayout } = useRailHelpers();

  return (
    <div className="flex h-full flex-col items-center gap-1 py-2 animate-in fade-in duration-200">
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Expand AI analyst"
              onClick={() => updateLayout({ rightCollapsed: false })}
            >
              <PanelRightOpen />
            </Button>
          }
        />
        <TooltipContent side="left">Expand analyst</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Open AI analyst"
              onClick={() => updateLayout({ rightCollapsed: false })}
              className="text-[color:var(--accent-cyan)]"
            >
              <Sparkles />
            </Button>
          }
        />
        <TooltipContent side="left">AI Analyst</TooltipContent>
      </Tooltip>
    </div>
  );
}

function useRailHelpers() {
  const { actions, updateLayout } = useWorkspace();
  return { actions, updateLayout };
}

/* ------------------------------------------------------------------ */
/* Drag handle                                                         */
/* ------------------------------------------------------------------ */

interface PanelResizeHandleProps {
  label: string;
  currentSize: number;
  onResize: (size: number) => void;
  onReset: () => void;
  /** Right panel: dragging left should grow the panel. */
  inverted?: boolean;
}

function PanelResizeHandle({
  label,
  currentSize,
  onResize,
  onReset,
  inverted = false,
}: PanelResizeHandleProps) {
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef({ pointerX: 0, size: 0 });

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    dragStart.current = { pointerX: event.clientX, size: currentSize };
    setDragging(true);
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    const delta = event.clientX - dragStart.current.pointerX;
    onResize(dragStart.current.size + (inverted ? -delta : delta));
  };

  const handlePointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragging(false);
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = inverted ? -24 : 24;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      onResize(currentSize - step);
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      onResize(currentSize + step);
    }
  };

  return (
    <div
      role="separator"
      aria-label={label}
      aria-orientation="vertical"
      tabIndex={0}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      onKeyDown={handleKeyDown}
      onDoubleClick={onReset}
      className={cn(
        "group relative z-10 -mx-1 w-2 shrink-0 cursor-col-resize outline-none",
        dragging && "select-none",
      )}
    >
      <span
        className={cn(
          "absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors",
          dragging
            ? "bg-[color:var(--accent-cyan)]"
            : "bg-transparent group-hover:bg-[color:var(--accent-cyan)]/60 group-focus-visible:bg-[color:var(--accent-cyan)]",
        )}
      />
    </div>
  );
}
