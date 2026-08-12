"use client";

import Link from "next/link";
import { BrainCircuit } from "lucide-react";

/** Home link and product mark, kept to one tile so the menus start early. */
export function AppBrand() {
  return (
    <Link
      href="/"
      aria-label="DocMind home"
      className="flex shrink-0 items-center gap-2 rounded-md px-1 py-1 text-sm font-semibold tracking-tight text-foreground outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50"
    >
      <span className="ai-avatar inline-flex size-6 items-center justify-center rounded-md">
        <BrainCircuit className="size-3.5" />
      </span>
      <span className="hidden lg:inline">DocMind</span>
    </Link>
  );
}
