"use client";

import { useInView } from "@/components/home/lib/use-in-view";

interface InViewProps {
  children: React.ReactNode;
  className?: string;
  /** Fraction of the element that must be visible before it plays. */
  threshold?: number;
}

/**
 * Flips `data-inview` once its subtree scrolls into view.
 *
 * This is the only client component the illustrations need: every entrance
 * animation is a CSS transition gated on `[data-inview="true"] .dm-*`, so the
 * artwork itself stays server-rendered and ships no JavaScript. `once` means an
 * illustration animates a single time and then costs nothing for the rest of
 * the session.
 */
export function InView({ children, className, threshold = 0.3 }: InViewProps) {
  const { ref, inView } = useInView<HTMLDivElement>({ threshold, once: true });

  return (
    <div ref={ref} data-inview={inView} className={className}>
      {children}
    </div>
  );
}
