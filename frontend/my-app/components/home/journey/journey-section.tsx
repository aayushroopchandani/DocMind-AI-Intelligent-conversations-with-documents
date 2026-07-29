import { Fragment } from "react";
import { JOURNEY } from "@/components/home/data/homepage-content";
import { JourneyCard } from "@/components/home/journey/journey-card";
import { InView } from "@/components/home/lib/in-view";
import { Reveal } from "@/components/reveal";

/**
 * The whole product in three frames: files in, analysis, a result worth
 * keeping.
 *
 * The steps sit in a flex row with the connectors as real siblings rather than
 * absolutely-positioned decoration, so the dashes land in the gaps at every
 * width without a single magic offset. Below `lg` the row stacks and the
 * connectors drop out entirely.
 */
export function JourneySection() {
  return (
    <section id="how-it-works" className="relative scroll-mt-24 py-24 sm:py-28">
      <div className="mx-auto max-w-6xl px-4">
        <Reveal className="mx-auto max-w-2xl text-center">
          <span className="dm-eyebrow">How it works</span>
          <h2 className="dm-headline mt-3 text-balance text-3xl font-semibold tracking-tight sm:text-[2.6rem] sm:leading-[1.1]">
            From raw files to a finished analysis
          </h2>
        </Reveal>

        <Reveal delay={0.08}>
          <div className="dm-panel mt-12 p-5 sm:p-7">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-stretch lg:gap-0">
              {JOURNEY.map((step, i) => (
                <Fragment key={step.id}>
                  {i > 0 && (
                    <InView className="hidden shrink-0 self-center px-3 lg:block">
                      <svg
                        viewBox="0 0 48 8"
                        className="h-2 w-12"
                        role="presentation"
                        aria-hidden
                      >
                        <path
                          className="dm-dash"
                          d="M1 4h46"
                          fill="none"
                          stroke="color-mix(in oklch, var(--illus) 60%, transparent)"
                          strokeWidth="2"
                          strokeLinecap="round"
                          style={{ "--i": i } as React.CSSProperties}
                        />
                      </svg>
                    </InView>
                  )}
                  <div className="min-w-0 flex-1">
                    <JourneyCard {...step} />
                  </div>
                </Fragment>
              ))}
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
