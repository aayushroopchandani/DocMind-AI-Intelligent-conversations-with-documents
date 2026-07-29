import { SECURITY } from "@/components/home/data/homepage-content";
import { SECURITY_ILLUSTRATIONS } from "@/components/home/illustrations";
import { InView } from "@/components/home/lib/in-view";
import { Reveal } from "@/components/reveal";

/**
 * Trust, stated plainly.
 *
 * Same card grammar as the feature grid — claim, explanation, illustration —
 * so the page keeps one rhythm instead of introducing a second card style for
 * the section people scrutinise most.
 */
export function SecuritySection() {
  return (
    <section
      id="security"
      className="relative scroll-mt-24 border-y border-border bg-card/20 py-24 sm:py-32"
    >
      <div className="mx-auto max-w-6xl px-4">
        <Reveal className="mx-auto max-w-2xl text-center">
          <span className="dm-eyebrow">Trust</span>
          <h2 className="dm-headline mt-3 text-balance text-3xl font-semibold tracking-tight sm:text-[2.6rem] sm:leading-[1.1]">
            Data privacy and security
          </h2>
          <p className="mt-4 text-balance text-muted-foreground">
            Your documents are the input to an analysis, not to a training set.
            Every run is scoped to what you selected and isolated from
            everything else.
          </p>
        </Reveal>

        <div className="mt-14 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {SECURITY.map(({ id, title, description }, i) => {
            const Illustration = SECURITY_ILLUSTRATIONS[id];

            return (
              <Reveal key={id} delay={i * 0.08} className="h-full">
                <article className="dm-card flex h-full flex-col p-6">
                  <h3 className="text-[17px] font-semibold leading-snug tracking-tight text-foreground">
                    {title}
                  </h3>
                  <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">
                    {description}
                  </p>

                  <div className="mt-auto pt-7">
                    <InView className="dm-stage h-[150px]">
                      <Illustration />
                    </InView>
                  </div>
                </article>
              </Reveal>
            );
          })}
        </div>
      </div>
    </section>
  );
}
