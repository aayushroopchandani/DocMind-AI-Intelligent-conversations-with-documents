import Link from "next/link";
import { ArrowRight, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Reveal } from "@/components/reveal";
import {
  ACCENT_VAR,
  SURFACES,
} from "@/components/home/data/homepage-content";
import { cn } from "@/lib/utils";

/**
 * The two ways into the product. Making the workspace and the chat reader
 * explicit, side-by-side choices is the main repositioning on this page:
 * document chat becomes one mode, not the whole product.
 */
export function SurfacesSection() {
  return (
    <section id="surfaces" className="relative scroll-mt-24 py-24 sm:py-32">
      <div className="mx-auto max-w-6xl px-4">
        <Reveal className="mx-auto max-w-2xl text-center">
          <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground/70">
            Two ways in
          </span>
          <h2 className="mt-3 text-balance text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
            Pick your surface
          </h2>
        </Reveal>

        <div className="mt-14 grid grid-cols-1 gap-4 lg:grid-cols-2">
          {SURFACES.map((surface, i) => (
            <Reveal key={surface.href} delay={i * 0.1} className="h-full">
              <div
                className={cn(
                  "bento-card flex h-full flex-col p-7 sm:p-8",
                  surface.primary && "lg:p-9",
                )}
                style={
                  {
                    "--card-accent": ACCENT_VAR[surface.accent],
                  } as React.CSSProperties
                }
              >
                <div className="mb-5 flex items-center gap-3">
                  <span className="accent-tile inline-flex size-11 items-center justify-center rounded-xl">
                    <surface.icon className="size-5" />
                  </span>
                  <span className="accent-text text-[11px] font-medium uppercase tracking-[0.16em]">
                    {surface.eyebrow}
                  </span>
                </div>

                <h3 className="mb-2.5 text-xl font-semibold tracking-tight text-foreground sm:text-2xl">
                  {surface.title}
                </h3>
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {surface.description}
                </p>

                <ul className="mt-6 space-y-2.5">
                  {surface.bullets.map((bullet) => (
                    <li
                      key={bullet}
                      className="flex items-start gap-2.5 text-sm text-foreground/75"
                    >
                      <Check className="accent-text mt-0.5 size-4 shrink-0" />
                      {bullet}
                    </li>
                  ))}
                </ul>

                <div className="mt-8 pt-2">
                  <Button
                    size="lg"
                    variant={surface.primary ? "default" : "outline"}
                    nativeButton={false}
                    render={<Link href={surface.href} />}
                    className="h-11 w-full gap-2 text-sm sm:w-auto sm:px-6"
                    data-icon="inline-end"
                  >
                    {surface.cta}
                    <ArrowRight className="size-4" />
                  </Button>
                </div>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
