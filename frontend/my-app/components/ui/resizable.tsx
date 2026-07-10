"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { GripVertical } from "lucide-react";
import { cn } from "@/lib/utils";

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

interface ResizableSplitProps {
  size: number;
  onSizeChange: (size: number) => void;
  minSize: number;
  maxSize: number;
  minSecondSize?: number;
  collapsedSize?: number;
  collapseAt?: number;
  first: ReactNode;
  second: ReactNode;
  className?: string;
  firstClassName?: string;
  secondClassName?: string;
  handleClassName?: string;
  handleLabel?: string;
}

/**
 * Shadcn-style resizable split with pixel constraints.
 *
 * The first pane is controlled by `size`. When `collapseAt` is supplied,
 * dragging below that width snaps to `collapsedSize`.
 */
function ResizableSplit({
  size,
  onSizeChange,
  minSize,
  maxSize,
  minSecondSize = 320,
  collapsedSize,
  collapseAt,
  first,
  second,
  className,
  firstClassName,
  secondClassName,
  handleClassName,
  handleLabel = "Resize panels",
}: ResizableSplitProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef({ pointerX: 0, size: 0 });
  const collapsed = collapseAt !== undefined && size <= collapseAt;
  const displaySize = collapsed ? (collapsedSize ?? size) : size;

  const normalizeSize = useCallback(
    (nextSize: number) => {
      const containerWidth = containerRef.current?.clientWidth ?? 0;
      const responsiveMax = containerWidth
        ? Math.min(maxSize, Math.max(minSize, containerWidth - minSecondSize))
        : maxSize;

      if (
        collapseAt !== undefined &&
        collapsedSize !== undefined &&
        nextSize <= collapseAt
      ) {
        return collapsedSize;
      }

      return clamp(nextSize, minSize, responsiveMax);
    },
    [collapseAt, collapsedSize, maxSize, minSecondSize, minSize],
  );

  const commitSize = useCallback(
    (nextSize: number) => onSizeChange(normalizeSize(nextSize)),
    [normalizeSize, onSizeChange],
  );

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    dragStart.current = { pointerX: event.clientX, size };
    setDragging(true);
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    const delta = event.clientX - dragStart.current.pointerX;
    commitSize(dragStart.current.size + delta);
  };

  const handlePointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragging(false);
  };

  useEffect(() => {
    const onResize = () => commitSize(size);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [commitSize, size]);

  return (
    <div
      ref={containerRef}
      className={cn(
        "flex h-full min-h-0 min-w-0 overflow-hidden",
        dragging && "select-none",
        className,
      )}
    >
      <div
        className={cn("min-h-0 shrink-0 overflow-hidden", firstClassName)}
        style={{ width: displaySize }}
      >
        {first}
      </div>

      <div
        role="separator"
        aria-label={handleLabel}
        aria-orientation="vertical"
        tabIndex={0}
        className={cn(
          "group relative z-10 flex w-2 shrink-0 cursor-col-resize items-center justify-center outline-none",
          "focus-visible:bg-[color:var(--accent-cyan)]/10",
          handleClassName,
        )}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") {
            event.preventDefault();
            commitSize(size - 24);
          }
          if (event.key === "ArrowRight") {
            event.preventDefault();
            commitSize(size + 24);
          }
        }}
      >
        <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border transition-colors group-hover:bg-[color:var(--accent-cyan)]/70 group-focus-visible:bg-[color:var(--accent-cyan)]" />
        <span className="rounded-full border border-border bg-card p-0.5 text-muted-foreground opacity-0 shadow-sm transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
          <GripVertical className="size-3" />
        </span>
      </div>

      <div className={cn("min-h-0 min-w-0 flex-1", secondClassName)}>
        {second}
      </div>
    </div>
  );
}

export { ResizableSplit };
