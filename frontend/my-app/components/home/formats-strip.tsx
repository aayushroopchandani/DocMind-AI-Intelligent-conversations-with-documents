import { FORMATS } from "@/components/home/data/homepage-content";

/**
 * Infinite marquee of supported inputs.
 *
 * The track holds the list twice and translates by exactly -50%, so the loop is
 * seamless. It's one composited transform — no JavaScript, no scroll listener.
 */
export function FormatsStrip() {
  return (
    <section className="relative border-y border-border bg-card/20 py-8">
      <div className="mx-auto max-w-6xl px-4">
        <p className="mb-5 text-center text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground/70">
          Reads and writes
        </p>
      </div>

      <div className="marquee-mask relative overflow-hidden">
        {/*
         * Spacing lives on each chip's right margin rather than a flex `gap`:
         * a gap would also sit *between* the two copies, so translating by
         * exactly -50% would jump by one gap on every loop.
         */}
        <div className="animate-marquee flex w-max">
          {[0, 1].map((copy) => (
            <div key={copy} className="flex shrink-0" aria-hidden={copy === 1}>
              {FORMATS.map((format) => (
                <span
                  key={format.label}
                  className="mr-3 flex shrink-0 items-center gap-2 rounded-full border border-border bg-card/60 px-4 py-2 text-sm text-muted-foreground"
                >
                  <format.icon className="size-4 text-foreground/70" />
                  {format.label}
                </span>
              ))}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
