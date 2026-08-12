"use client";

import { Ban } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Cell colours are workbook data, not app chrome: they have to survive an
 * export into Excel or Sheets, so they are fixed sRGB hexes rather than the
 * theme's oklch tokens (which mean nothing outside this app).
 */
const SWATCH_ROWS: readonly (readonly string[])[] = [
  ["#ffffff", "#f3f4f6", "#d1d5db", "#9ca3af", "#4b5563", "#1f2937", "#000000"],
  ["#fecaca", "#fed7aa", "#fef08a", "#bbf7d0", "#a5f3fc", "#bfdbfe", "#e9d5ff"],
  ["#ef4444", "#f97316", "#eab308", "#22c55e", "#06b6d4", "#3b82f6", "#8b5cf6"],
  ["#991b1b", "#9a3412", "#854d0e", "#166534", "#155e75", "#1e40af", "#5b21b6"],
];

interface ColorSwatchGridProps {
  label: string;
  /** Shows a reset tile ("Default", "No fill") that reports null. */
  resetLabel?: string;
  onSelect: (color: string | null) => void;
}

/** Colour picker rendered inside a menu — a grid, not a list of names. */
export function ColorSwatchGrid({
  label,
  resetLabel,
  onSelect,
}: ColorSwatchGridProps) {
  return (
    <div className="p-1" role="group" aria-label={label}>
      {resetLabel ? (
        <button
          type="button"
          onClick={() => onSelect(null)}
          className="mb-1.5 flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-sm text-muted-foreground outline-none transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent focus-visible:text-accent-foreground"
        >
          <Ban className="size-4" />
          {resetLabel}
        </button>
      ) : null}

      <div className="flex flex-col gap-1">
        {SWATCH_ROWS.map((row, rowIndex) => (
          <div key={rowIndex} className="flex gap-1">
            {row.map((color) => (
              <button
                key={color}
                type="button"
                aria-label={color}
                title={color}
                onClick={() => onSelect(color)}
                style={{ backgroundColor: color }}
                className={cn(
                  "size-5 rounded-[5px] ring-1 ring-foreground/15 transition-transform outline-none",
                  "hover:scale-110 focus-visible:scale-110 focus-visible:ring-2 focus-visible:ring-[color:var(--accent-cyan)]",
                )}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
