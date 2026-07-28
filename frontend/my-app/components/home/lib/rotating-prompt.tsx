"use client";

import { useEffect, useState } from "react";
import { useInView } from "@/components/home/lib/use-in-view";
import { useReducedMotion } from "@/components/home/lib/use-reduced-motion";
import { cn } from "@/lib/utils";

/* Per-character and per-state timings, in milliseconds. */
const TYPE_MS = 34;
const DELETE_MS = 15;
const HOLD_MS = 2000;
const SWAP_MS = 260;

interface RotatingPromptProps {
  prompts: readonly string[];
  className?: string;
}

/**
 * Types through a list of example prompts, deleting between each.
 *
 * Only one timeout is ever pending, and it stops entirely when the element
 * leaves the viewport. Under reduced motion the first prompt is shown as
 * static text.
 */
export function RotatingPrompt({ prompts, className }: RotatingPromptProps) {
  const { ref, inView } = useInView<HTMLSpanElement>({ threshold: 0 });
  const reduced = useReducedMotion();

  const [index, setIndex] = useState(0);
  const [count, setCount] = useState(0);
  const [deleting, setDeleting] = useState(false);

  const text = prompts[index] ?? "";

  useEffect(() => {
    if (reduced || !inView) return;

    const typing = !deleting && count < text.length;
    const holding = !deleting && count >= text.length;
    const erasing = deleting && count > 0;

    const delay = typing
      ? TYPE_MS
      : holding
        ? HOLD_MS
        : erasing
          ? DELETE_MS
          : SWAP_MS;

    const id = window.setTimeout(() => {
      if (typing) setCount((c) => c + 1);
      else if (holding) setDeleting(true);
      else if (erasing) setCount((c) => c - 1);
      else {
        setDeleting(false);
        setIndex((i) => (i + 1) % prompts.length);
      }
    }, delay);

    return () => window.clearTimeout(id);
  }, [count, deleting, text, inView, reduced, prompts.length]);

  return (
    <span ref={ref} className={cn("type-caret", className)}>
      {reduced ? prompts[0] : text.slice(0, count)}
    </span>
  );
}
