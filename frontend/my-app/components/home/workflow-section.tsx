import {
  ACCENT_VAR,
  WORKFLOW,
} from "@/components/home/data/homepage-content";
import { Reveal } from "@/components/reveal";

/**
 * The workspace loop, five steps wide.
 *
 * The connecting rail is a single absolutely-positioned gradient line behind
 * the cards rather than per-card borders, so it stays unbroken at every width
 * it's visible at.
 */
export function WorkflowSection() {
  return (
    <section
      id="how-it-works"
      className="relative scroll-mt-24 border-y border-border bg-card/30 py-24 sm:py-32"
    >
      <div className="mx-auto max-w-6xl px-4">
        <Reveal className="mx-auto max-w-2xl text-center">
          <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground/70">
            The loop
          </span>
          <h2 className="mt-3 text-balance text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
            Select, ask, preview, apply
          </h2>
          <p className="mt-4 text-balance text-muted-foreground">
            Analysis happens against the context you choose — and nothing
            changes until you say so.
          </p>
        </Reveal>

        <div className="relative mt-16">
          {/* Connecting rail behind the step cards. */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 top-[3.25rem] hidden h-px lg:block"
            style={{
              background:
                "linear-gradient(to right, transparent, var(--border) 12%, var(--border) 88%, transparent)",
            }}
          />

          {/* Plain divs, not an <ol>: `Reveal` renders a wrapper element, and
              <ol> may only contain <li> children. */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {WORKFLOW.map((item, i) => (
              <Reveal key={item.step} delay={i * 0.08} className="h-full">
                <article
                  className="bento-card group flex h-full flex-col p-5"
                  style={
                    {
                      "--card-accent": ACCENT_VAR[item.accent],
                    } as React.CSSProperties
                  }
                >
                  <div className="mb-4 flex items-center justify-between">
                    <span className="accent-tile inline-flex size-9 items-center justify-center rounded-lg">
                      <item.icon className="size-4" />
                    </span>
                    <span className="font-mono text-xs text-muted-foreground/60">
                      {item.step}
                    </span>
                  </div>
                  <h3 className="mb-1.5 text-sm font-semibold tracking-tight text-foreground">
                    {item.title}
                  </h3>
                  <p className="text-[13px] leading-relaxed text-muted-foreground">
                    {item.description}
                  </p>
                </article>
              </Reveal>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
