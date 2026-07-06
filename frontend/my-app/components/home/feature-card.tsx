"use client";

import type { LucideIcon } from "lucide-react";
import SpotlightCard from "@/components/SpotlightCard";

export interface FeatureCardProps {
  icon: LucideIcon;
  title: string;
  description: string;
}

/**
 * A single feature tile with a cursor-following spotlight glow (React Bits)
 * and a hover lift. Monochrome by design — the glow is a soft white.
 */
export function FeatureCard({ icon: Icon, title, description }: FeatureCardProps) {
  return (
    <SpotlightCard
      spotlightColor="rgba(255, 255, 255, 0.10)"
      className="group h-full !border-border !bg-card p-6 transition-transform duration-300 hover:-translate-y-1"
    >
      <div className="mb-4 inline-flex size-11 items-center justify-center rounded-xl border border-border bg-background/60 text-foreground transition-colors group-hover:bg-foreground group-hover:text-background">
        <Icon className="size-5" />
      </div>
      <h3 className="mb-2 text-base font-semibold tracking-tight text-foreground">
        {title}
      </h3>
      <p className="text-sm leading-relaxed text-muted-foreground">
        {description}
      </p>
    </SpotlightCard>
  );
}
