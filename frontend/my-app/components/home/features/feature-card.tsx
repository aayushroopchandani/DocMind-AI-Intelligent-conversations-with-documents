import type { Feature } from "@/components/home/data/homepage-content";
import { FEATURE_ILLUSTRATIONS } from "@/components/home/illustrations";
import { InView } from "@/components/home/lib/in-view";
import { cn } from "@/lib/utils";

/**
 * One illustrated feature tile: claim first, evidence underneath.
 *
 * The illustration sits in a fixed-height stage so a row of cards keeps a
 * shared baseline however long the copy runs, and `InView` gates the artwork's
 * entrance so nothing animates until it is actually on screen.
 */
export function FeatureCard({
  id,
  title,
  description,
  className,
}: Feature & { className?: string }) {
  const Illustration = FEATURE_ILLUSTRATIONS[id];

  return (
    <article className={cn("dm-card flex h-full flex-col p-6", className)}>
      <h3 className="text-[17px] font-semibold leading-snug tracking-tight text-foreground">
        {title}
      </h3>
      <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">
        {description}
      </p>

      <div className="mt-auto pt-7">
        <InView className="dm-stage h-[188px]">
          <Illustration />
        </InView>
      </div>
    </article>
  );
}
