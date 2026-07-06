"use client";

import {
  ChevronLeft,
  ChevronRight,
  ZoomIn,
  ZoomOut,
  Scaling,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface PdfControlsProps {
  currentPage: number;
  numPages: number;
  onPrev: () => void;
  onNext: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitWidth: () => void;
  fitWidth: boolean;
  disabled?: boolean;
}

function ControlButton({
  onClick,
  disabled,
  label,
  active,
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  label: string;
  active?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      aria-pressed={active}
      className={cn(
        "inline-flex size-8 items-center justify-center rounded-lg border border-transparent text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-30",
        active && "border-border bg-accent text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/** Toolbar for page navigation and zoom, shown above the PDF canvas. */
export function PdfControls({
  currentPage,
  numPages,
  onPrev,
  onNext,
  onZoomIn,
  onZoomOut,
  onFitWidth,
  fitWidth,
  disabled = false,
}: PdfControlsProps) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-border bg-card/60 px-3 py-2">
      <div className="flex items-center gap-1">
        <ControlButton onClick={onPrev} disabled={disabled || currentPage <= 1} label="Previous page">
          <ChevronLeft className="size-4" />
        </ControlButton>
        <span className="min-w-24 text-center text-xs tabular-nums text-muted-foreground">
          {disabled ? "—" : `Page ${currentPage} / ${numPages || "…"}`}
        </span>
        <ControlButton
          onClick={onNext}
          disabled={disabled || currentPage >= numPages}
          label="Next page"
        >
          <ChevronRight className="size-4" />
        </ControlButton>
      </div>

      <div className="flex items-center gap-1">
        <ControlButton onClick={onZoomOut} disabled={disabled} label="Zoom out">
          <ZoomOut className="size-4" />
        </ControlButton>
        <ControlButton onClick={onZoomIn} disabled={disabled} label="Zoom in">
          <ZoomIn className="size-4" />
        </ControlButton>
        <ControlButton
          onClick={onFitWidth}
          disabled={disabled}
          label="Fit width"
          active={fitWidth}
        >
          <Scaling className="size-4" />
        </ControlButton>
      </div>
    </div>
  );
}
