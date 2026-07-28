import { CapabilityCard } from "@/components/home/capabilities/capability-card";
import { CAPABILITIES } from "@/components/home/data/homepage-content";
import { Reveal } from "@/components/reveal";
import { cn } from "@/lib/utils";

/**
 * The capability bento. Tiles vary in width (`span` in the content file) so the
 * grid reads as a composed layout rather than six identical boxes.
 */
export function CapabilitiesSection() {
  return (
    <section
      id="features"
      className="section-aurora relative scroll-mt-24 py-24 sm:py-32"
    >
      <div className="mx-auto max-w-6xl px-4">
        <Reveal className="mx-auto max-w-2xl text-center">
          <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground/70">
            Capabilities
          </span>
          <h2 className="prism-text mt-3 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
            An analyst that produces artifacts, not paragraphs
          </h2>
          <p className="mt-4 text-balance text-muted-foreground">
            Ask a question and get back a chart, a cleaned dataset, a new
            worksheet or a formula — inside the workspace, with a trail back to
            the source.
          </p>
        </Reveal>

        <div className="mt-14 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-12">
          {CAPABILITIES.map(({ span, ...capability }, i) => (
            <Reveal
              key={capability.title}
              delay={(i % 3) * 0.08}
              className={cn("h-full", span)}
            >
              <CapabilityCard {...capability} />
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
