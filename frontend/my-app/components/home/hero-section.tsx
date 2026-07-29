"use client";

import Link from "next/link";
import { ArrowRight, MessageSquareText, Search } from "lucide-react";
import Prism from "@/components/Prism";
import SplitText from "@/components/SplitText";
import { Button } from "@/components/ui/button";
import { AgentConsole } from "@/components/home/agent-demo/agent-console";
import { RotatingPrompt } from "@/components/home/lib/rotating-prompt";
import { HERO_PROMPTS } from "@/components/home/data/homepage-content";

/**
 * Landing hero.
 *
 * The Prism spectrum (React Bits, WebGL) is the only GPU-backed effect on the
 * page — everything below it is CSS and SVG — which keeps the homepage to a
 * single canvas and well clear of the context limits that make multi-shader
 * landing pages tab-crashers.
 */
export function HeroSection() {
  return (
    <section className="relative isolate overflow-hidden pb-20 pt-36 sm:pb-28 sm:pt-44">
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <Prism
          animationType="rotate"
          timeScale={0.5}
          height={3.5}
          baseWidth={5.5}
          scale={3.6}
          glow={1}
          noise={0}
          bloom={1}
          hueShift={0}
          colorFrequency={1}
          transparent
          suspendWhenOffscreen
        />
      </div>

      {/* Dark radial overlay — keeps centre text legible over the bright prism. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          background:
            "radial-gradient(ellipse 80% 55% at 50% 35%, rgba(0,0,0,0.62) 0%, rgba(0,0,0,0.14) 60%, transparent 100%)",
        }}
      />

      {/* Bottom-fade blends the prism into the page background. */}
      <div
        aria-hidden
        className="hero-fade pointer-events-none absolute inset-0 -z-10"
      />

      <div className="mx-auto max-w-6xl px-4">
        <div className="mx-auto flex max-w-3xl flex-col items-center text-center">
          {/*
           * Positioning pill. A tag plus a plain-language clause reads as a
           * product descriptor; the blinking status dot it replaces read as a
           * service-health badge, which is not what this is.
           */}
          <span className="dm-badge mb-6 inline-flex items-center gap-2.5 rounded-full py-1 pl-1 pr-3.5 text-xs">
            <span className="dm-badge-tag rounded-full px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.14em] text-foreground/85">
              Analysis agent
            </span>
            <span className="text-muted-foreground">
              PDFs, spreadsheets and CSVs in one workspace
            </span>
          </span>

          <SplitText
            text="An AI analyst for your documents and data."
            tag="h1"
            className="text-balance text-4xl font-semibold leading-[1.05] tracking-tight text-foreground sm:text-6xl"
            splitType="words"
            delay={40}
            duration={0.9}
            ease="power3.out"
            from={{ opacity: 0, y: 40 }}
            to={{ opacity: 1, y: 0 }}
            threshold={0.2}
          />

          <p className="mt-6 max-w-2xl text-balance text-base leading-relaxed text-muted-foreground sm:text-lg">
            DocMind opens PDFs, spreadsheets and CSVs in one workspace — then
            cleans the data, writes the formulas, builds the charts, and cites
            every number back to the page or cell it came from.
          </p>

          {/* Rotating example prompt, styled as the analyst composer. */}
          <div className="mt-8 flex w-full max-w-lg items-center gap-2.5 rounded-xl border border-border bg-card/60 px-3.5 py-2.5 text-left backdrop-blur">
            <Search className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="min-h-[1.25rem] min-w-0 flex-1 truncate text-sm text-foreground/80">
              <RotatingPrompt prompts={HERO_PROMPTS} />
            </span>
            <kbd className="hidden shrink-0 rounded border border-border bg-background/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline">
              ⏎
            </kbd>
          </div>

          <div className="mt-7 flex flex-col items-center gap-3 sm:flex-row">
            <Button
              size="lg"
              nativeButton={false}
              render={<Link href="/data-analysis" />}
              className="h-11 gap-2 px-6 text-sm"
              data-icon="inline-end"
            >
              Open Analysis Workspace
              <ArrowRight className="size-4" />
            </Button>
            <Button
              size="lg"
              variant="outline"
              nativeButton={false}
              render={<Link href="/chat" />}
              className="h-11 gap-2 px-6 text-sm"
              data-icon="inline-start"
            >
              <MessageSquareText className="size-4" />
              Chat with a PDF
            </Button>
          </div>
        </div>

        <div className="animate-float relative mx-auto mt-16 max-w-4xl sm:mt-20">
          <AgentConsole />
        </div>
      </div>
    </section>
  );
}
