"use client";

import { useState, type FormEvent, type KeyboardEvent } from "react";
import {
  ChevronDown,
  ChevronUp,
  Columns2,
  Download,
  Ellipsis,
  Hand,
  MoveHorizontal,
  MoveVertical,
  MousePointer2,
  PanelLeft,
  RotateCcw,
  RotateCw,
  Scan,
  Search,
  Square,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useExport } from "@embedpdf/plugin-export/react";
import { usePan } from "@embedpdf/plugin-pan/react";
import { useRotate } from "@embedpdf/plugin-rotate/react";
import { useScroll, ScrollStrategy } from "@embedpdf/plugin-scroll/react";
import { SpreadMode, useSpread } from "@embedpdf/plugin-spread/react";
import {
  useZoom,
  useZoomCapability,
  ZoomMode,
} from "@embedpdf/plugin-zoom/react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { PdfSearchPanel } from "@/components/data-analysis/workspace/pdf/pdf-search-panel";
import { cn } from "@/lib/utils";

interface PdfToolbarProps {
  documentId: string;
  documentName: string;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  /** False when the column is too narrow to fit thumbnails and a page. */
  canToggleSidebar: boolean;
  searchOpen: boolean;
  onSearchOpenChange: (open: boolean) => void;
  /** Below this the toolbar folds secondary controls into the "More" menu. */
  isCompact: boolean;
}

/**
 * Compact PDF toolbar built from DocMind's own shadcn primitives.
 *
 * EmbedPDF supplies behaviour only (through its hooks) — none of its default
 * chrome is used, so the viewer reads as part of the workspace rather than an
 * embedded third-party widget. At narrow widths the zoom stepper, rotation,
 * layout and download controls collapse into the "More" dropdown, leaving
 * page navigation and search always reachable.
 */
export function PdfToolbar({
  documentId,
  documentName,
  sidebarOpen,
  onToggleSidebar,
  canToggleSidebar,
  searchOpen,
  onSearchOpenChange,
  isCompact,
}: PdfToolbarProps) {
  const { provides: scroll, state: scrollState } = useScroll(documentId);
  const { provides: zoom, state: zoomState } = useZoom(documentId);
  // Presets are plugin-wide config, so they live on the capability rather
  // than the per-document scope.
  const { provides: zoomCapability } = useZoomCapability();
  const { provides: rotate } = useRotate(documentId);
  const { provides: pan, isPanning } = usePan(documentId);
  const { spreadMode, provides: spread } = useSpread(documentId);
  const { provides: exportApi } = useExport(documentId);

  const zoomPercent = Math.round(zoomState.currentZoomLevel * 100);

  return (
    <div className="flex h-10 shrink-0 items-center gap-0.5 border-b border-border bg-card/30 px-1.5">
      {canToggleSidebar ? (
        <>
          <IconAction
            label={sidebarOpen ? "Hide page thumbnails" : "Show page thumbnails"}
            onClick={onToggleSidebar}
            pressed={sidebarOpen}
          >
            <PanelLeft />
          </IconAction>
          <Separator orientation="vertical" className="mx-1 h-5" />
        </>
      ) : null}

      <PageNavigator
        currentPage={scrollState.currentPage}
        totalPages={scrollState.totalPages}
        onGoToPage={(pageNumber) => scroll?.scrollToPage({ pageNumber })}
        onPrevious={() => scroll?.scrollToPreviousPage()}
        onNext={() => scroll?.scrollToNextPage()}
      />

      {!isCompact ? (
        <>
          <Separator orientation="vertical" className="mx-1 h-5" />

          <IconAction
            label="Zoom out"
            onClick={() => zoom?.zoomOut()}
            disabled={!zoom}
          >
            <ZoomOut />
          </IconAction>

          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  variant="ghost"
                  size="xs"
                  aria-label={`Zoom level, currently ${zoomPercent}%`}
                  className="min-w-14 tabular-nums"
                >
                  {zoomPercent}%
                </Button>
              }
            />
            <DropdownMenuContent align="start" className="w-40">
              <DropdownMenuLabel>Zoom</DropdownMenuLabel>
              {zoomCapability?.getPresets().map((preset) => (
                <DropdownMenuItem
                  key={preset.name}
                  onClick={() => zoom?.requestZoom(preset.value)}
                >
                  {preset.name}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          <IconAction
            label="Zoom in"
            onClick={() => zoom?.zoomIn()}
            disabled={!zoom}
          >
            <ZoomIn />
          </IconAction>

          <IconAction
            label="Fit page"
            onClick={() => zoom?.requestZoom(ZoomMode.FitPage)}
            pressed={zoomState.zoomLevel === ZoomMode.FitPage}
          >
            <Scan />
          </IconAction>
          <IconAction
            label="Fit width"
            onClick={() => zoom?.requestZoom(ZoomMode.FitWidth)}
            pressed={zoomState.zoomLevel === ZoomMode.FitWidth}
          >
            <MoveHorizontal />
          </IconAction>
        </>
      ) : null}

      <div className="min-w-2 flex-1" />

      <p className="hidden min-w-0 max-w-56 truncate px-2 text-xs text-muted-foreground xl:block">
        {documentName}
      </p>

      <IconAction
        label={isPanning ? "Text selection tool" : "Hand tool — drag to pan"}
        onClick={() => pan?.togglePan()}
        pressed={isPanning}
      >
        {isPanning ? <Hand /> : <MousePointer2 />}
      </IconAction>

      <PdfSearchPanel
        documentId={documentId}
        open={searchOpen}
        onOpenChange={onSearchOpenChange}
        trigger={
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Search document"
            aria-pressed={searchOpen}
            className={cn(searchOpen && "bg-muted text-foreground")}
          >
            <Search />
          </Button>
        }
      />

      {!isCompact ? (
        <>
          <IconAction label="Rotate left" onClick={() => rotate?.rotateBackward()}>
            <RotateCcw />
          </IconAction>
          <IconAction label="Rotate right" onClick={() => rotate?.rotateForward()}>
            <RotateCw />
          </IconAction>
          <IconAction
            label="Download original PDF"
            onClick={() => exportApi?.download()}
            disabled={!exportApi}
          >
            <Download />
          </IconAction>
        </>
      ) : null}

      {/* Overflow menu: page layout always, plus whatever the compact
          breakpoint pulled out of the strip. */}
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="ghost" size="icon-sm" aria-label="More PDF options">
              <Ellipsis />
            </Button>
          }
        />
        <DropdownMenuContent align="end" className="w-52">
          <DropdownMenuLabel>Page layout</DropdownMenuLabel>
          <DropdownMenuItem onClick={() => spread?.setSpreadMode(SpreadMode.None)}>
            <Square
              className={cn(
                spreadMode === SpreadMode.None &&
                  "text-[color:var(--accent-cyan)]",
              )}
            />
            Single page
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => spread?.setSpreadMode(SpreadMode.Odd)}>
            <Columns2
              className={cn(
                spreadMode === SpreadMode.Odd &&
                  "text-[color:var(--accent-cyan)]",
              )}
            />
            Two pages
          </DropdownMenuItem>

          <DropdownMenuSeparator />
          <DropdownMenuLabel>Scrolling</DropdownMenuLabel>
          <DropdownMenuItem
            onClick={() => scroll?.setScrollStrategy(ScrollStrategy.Vertical)}
          >
            <MoveVertical />
            Vertical
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => scroll?.setScrollStrategy(ScrollStrategy.Horizontal)}
          >
            <MoveHorizontal />
            Horizontal
          </DropdownMenuItem>

          {isCompact ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuLabel>Zoom &amp; rotation</DropdownMenuLabel>
              <DropdownMenuItem onClick={() => zoom?.zoomOut()}>
                <ZoomOut />
                Zoom out
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => zoom?.zoomIn()}>
                <ZoomIn />
                Zoom in ({zoomPercent}%)
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => zoom?.requestZoom(ZoomMode.FitPage)}
              >
                <Scan />
                Fit page
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => zoom?.requestZoom(ZoomMode.FitWidth)}
              >
                <MoveHorizontal />
                Fit width
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => rotate?.rotateBackward()}>
                <RotateCcw />
                Rotate left
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => rotate?.rotateForward()}>
                <RotateCw />
                Rotate right
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => exportApi?.download()}>
                <Download />
                Download original
              </DropdownMenuItem>
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Page navigation                                                     */
/* ------------------------------------------------------------------ */

interface PageNavigatorProps {
  currentPage: number;
  totalPages: number;
  onGoToPage: (pageNumber: number) => void;
  onPrevious: () => void;
  onNext: () => void;
}

function PageNavigator({
  currentPage,
  totalPages,
  onGoToPage,
  onPrevious,
  onNext,
}: PageNavigatorProps) {
  /**
   * `null` means "follow the document". A draft only exists while the user is
   * typing, so scrolling keeps the field live without an effect syncing it —
   * and typing "12" never navigates through page 1 on the way.
   */
  const [draft, setDraft] = useState<string | null>(null);
  const value = draft ?? String(currentPage);

  const commit = () => {
    if (draft === null) return;
    const parsed = Number.parseInt(draft, 10);
    // Reverting to `null` snaps the field back to the real current page.
    setDraft(null);
    if (Number.isFinite(parsed) && parsed >= 1 && parsed <= totalPages) {
      onGoToPage(parsed);
    }
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    commit();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      setDraft(null);
      event.currentTarget.blur();
    }
  };

  return (
    <div className="flex items-center gap-0.5">
      <IconAction
        label="Previous page"
        onClick={onPrevious}
        disabled={currentPage <= 1}
      >
        <ChevronUp />
      </IconAction>
      <form onSubmit={handleSubmit} className="flex items-center gap-1">
        <Input
          value={value}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          onKeyDown={handleKeyDown}
          inputMode="numeric"
          aria-label={`Page number, ${currentPage} of ${totalPages}`}
          className="h-6 w-11 px-1 text-center text-xs tabular-nums"
        />
        <span className="whitespace-nowrap text-xs tabular-nums text-muted-foreground">
          / {totalPages || "–"}
        </span>
      </form>
      <IconAction
        label="Next page"
        onClick={onNext}
        disabled={totalPages > 0 && currentPage >= totalPages}
      >
        <ChevronDown />
      </IconAction>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Shared button                                                       */
/* ------------------------------------------------------------------ */

interface IconActionProps {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
  pressed?: boolean;
}

/** Icon-only toolbar button: real `<button>`, ARIA label and tooltip. */
function IconAction({
  label,
  onClick,
  children,
  disabled,
  pressed,
}: IconActionProps) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={label}
            aria-pressed={pressed}
            disabled={disabled}
            onClick={onClick}
            className={cn(pressed && "bg-muted text-foreground")}
          >
            {children}
          </Button>
        }
      />
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}
