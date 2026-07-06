"use client";

import AnimatedContent from "@/components/AnimatedContent";

interface RevealProps {
  children: React.ReactNode;
  className?: string;
  /** Stagger helper: seconds to wait before the reveal plays. */
  delay?: number;
  distance?: number;
  direction?: "vertical" | "horizontal";
}

/**
 * Thin, ergonomic wrapper over the React Bits `AnimatedContent` component so
 * server sections can opt into a consistent scroll-reveal without repeating props.
 */
export function Reveal({
  children,
  className,
  delay = 0,
  distance = 60,
  direction = "vertical",
}: RevealProps) {
  return (
    <AnimatedContent
      className={className}
      distance={distance}
      direction={direction}
      duration={0.9}
      ease="power3.out"
      initialOpacity={0}
      animateOpacity
      threshold={0.15}
      delay={delay}
    >
      {children}
    </AnimatedContent>
  );
}
