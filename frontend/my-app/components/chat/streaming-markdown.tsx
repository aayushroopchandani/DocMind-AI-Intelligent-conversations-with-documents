"use client";

import { memo, useLayoutEffect, useRef, useState } from "react";
import { Markdown } from "@/components/chat/markdown";
import { cn } from "@/lib/utils";

interface StreamingMarkdownProps {
  content: string;
  isStreaming: boolean;
}

interface CursorPosition {
  top: number;
  left: number;
  height: number;
}

function measureCursorPosition(container: HTMLElement): CursorPosition {
  const containerRect = container.getBoundingClientRect();

  const textNodes: Text[] = [];
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  let node: Node | null = walker.nextNode();
  while (node) {
    const text = node as Text;
    if (text.data.replace(/\u200B/g, "").trim().length > 0) {
      textNodes.push(text);
    }
    node = walker.nextNode();
  }

  if (textNodes.length === 0) {
    return { top: 2, left: 0, height: 16 };
  }

  const lastText = textNodes[textNodes.length - 1]!;
  const range = document.createRange();
  range.setStart(lastText, lastText.length);
  range.collapse(true);

  const rects = range.getClientRects();
  const rect =
    rects.length > 0
      ? rects[rects.length - 1]!
      : range.getBoundingClientRect();

  return {
    top: rect.top - containerRect.top,
    left: rect.right - containerRect.left + 1,
    height: Math.max(rect.height || 16, 14),
  };
}

/**
 * Renders markdown with a caret that tracks the end of the live stream.
 * The typing effect comes from real token chunks — this only positions the
 * blinking cursor inline at the last rendered character.
 */
export const StreamingMarkdown = memo(function StreamingMarkdown({
  content,
  isStreaming,
}: StreamingMarkdownProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [cursorPos, setCursorPos] = useState<CursorPosition | null>(null);
  const [pulse, setPulse] = useState(false);
  const prevLengthRef = useRef(0);

  useLayoutEffect(() => {
    if (!isStreaming) {
      setCursorPos(null);
      prevLengthRef.current = content.length;
      return;
    }

    const container = containerRef.current;
    if (!container) return;

    setCursorPos(measureCursorPosition(container));

    if (content.length > prevLengthRef.current) {
      setPulse(true);
      const timer = window.setTimeout(() => setPulse(false), 180);
      prevLengthRef.current = content.length;
      return () => window.clearTimeout(timer);
    }

    prevLengthRef.current = content.length;
  }, [content, isStreaming]);

  return (
    <div
      ref={containerRef}
      className={cn(
        "streaming-markdown",
        isStreaming && "streaming-markdown--active",
        pulse && "streaming-markdown--pulse",
      )}
    >
      <Markdown content={content} />
      {isStreaming && cursorPos ? (
        <span
          className="streaming-cursor"
          style={{
            top: cursorPos.top,
            left: cursorPos.left,
            height: cursorPos.height,
          }}
          aria-hidden
        />
      ) : null}
    </div>
  );
});
