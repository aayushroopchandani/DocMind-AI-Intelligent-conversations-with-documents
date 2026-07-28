"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

interface TypingTextProps {
  text: string;
  /** Start typing. Callers remount via `key` to replay, rather than toggling. */
  run: boolean;
  /** Skip the animation and render the full string (reduced motion). */
  instant?: boolean;
  /** Milliseconds per character. */
  speed?: number;
  /** Show a blinking caret while characters are still arriving. */
  caret?: boolean;
  className?: string;
}

/**
 * Types a string one character at a time.
 *
 * State lives here rather than in the parent so a ~50-character line re-renders
 * a single leaf node, not the whole console. There is no reset path: to replay
 * the line, give the element a new `key` — a fresh mount starts at zero.
 */
export function TypingText({
  text,
  run,
  instant = false,
  speed = 26,
  caret = true,
  className,
}: TypingTextProps) {
  const [count, setCount] = useState(0);
  const done = count >= text.length;

  useEffect(() => {
    if (instant || !run || count >= text.length) return;

    // Pause a beat longer on spaces so the cadence reads as human.
    const delay = text[count] === " " ? speed + 40 : speed;
    const id = window.setTimeout(() => setCount((c) => c + 1), delay);
    return () => window.clearTimeout(id);
  }, [run, instant, count, text, speed]);

  const visible = instant ? text : run ? text.slice(0, count) : "";

  return (
    <span
      className={cn(caret && !instant && run && !done && "type-caret", className)}
      aria-label={text}
    >
      {visible}
    </span>
  );
}
