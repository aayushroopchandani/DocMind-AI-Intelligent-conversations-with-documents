import { FEATURES } from "@/components/home/data/homepage-content";
import { FeatureCard } from "@/components/home/features/feature-card";
import { FeatureHighlights } from "@/components/home/features/feature-highlights";
import { Reveal } from "@/components/reveal";

/**
 * What the analyst actually produces.
 *
 * Six equal tiles rather than a ragged bento: every card carries an
 * illustration of comparable weight, so a uniform grid reads as considered
 * instead of repetitive, and nothing has to be tuned per breakpoint.
 */
export function FeaturesSection() {
  return (
    <section id="features" className="relative scroll-mt-24 py-24 sm:py-32">
      <div className="mx-auto max-w-6xl px-4">
        <Reveal className="mx-auto max-w-2xl text-center">
          <span className="dm-eyebrow">Capabilities</span>
          <h2 className="dm-headline mt-3 text-balance text-3xl font-semibold tracking-tight sm:text-[2.6rem] sm:leading-[1.1]">
            Every answer arrives as a working artifact
          </h2>
          <p className="mt-4 text-balance text-muted-foreground">
            Ask a question and get back a chart, a cleaned dataset, a new
            worksheet or a formula — inside the workspace, with a trail back to
            the source.
          </p>
        </Reveal>

        <div className="mt-14 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((feature, i) => (
            <Reveal
              key={feature.id}
              delay={(i % 3) * 0.08}
              className="h-full"
            >
              <FeatureCard {...feature} />
            </Reveal>
          ))}
        </div>

        <FeatureHighlights />
      </div>
    </section>
  );
}
