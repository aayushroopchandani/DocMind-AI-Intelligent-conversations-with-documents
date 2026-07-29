import { HIGHLIGHTS } from "@/components/home/data/homepage-content";
import { Reveal } from "@/components/reveal";

/**
 * The supporting capabilities, as a quiet four-up row.
 *
 * Deliberately unillustrated: giving every feature the same visual weight is
 * what made the old grid read as a wall of boxes. These are real, but they are
 * not the headline.
 */
export function FeatureHighlights() {
  return (
    <div className="mt-4 grid grid-cols-1 gap-px overflow-hidden rounded-2xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
      {HIGHLIGHTS.map(({ icon: Icon, title, description }, i) => (
        <Reveal key={title} delay={i * 0.06} className="h-full">
          <div className="flex h-full flex-col gap-2 bg-card/40 p-5">
            <Icon className="size-4" style={{ color: "var(--illus)" }} />
            <h3 className="text-[13px] font-semibold tracking-tight text-foreground">
              {title}
            </h3>
            <p className="text-[12.5px] leading-relaxed text-muted-foreground">
              {description}
            </p>
          </div>
        </Reveal>
      ))}
    </div>
  );
}
