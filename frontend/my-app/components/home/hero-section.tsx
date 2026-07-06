"use client";

import Link from "next/link";
import { ArrowRight, PlayCircle } from "lucide-react";
import Prism from "@/components/Prism";
import SplitText from "@/components/SplitText";
import { Button } from "@/components/ui/button";
import { ProductMockup } from "@/components/home/product-mockup";

/**
 * Landing hero: a full-colour Prism spectrum background (React Bits),
 * a SplitText animated heading, supporting copy, CTAs, and a product mockup.
 */
export function HeroSection() {
  return (
    <section className="relative isolate overflow-hidden pb-20 pt-36 sm:pb-28 sm:pt-44">
      {/*
       * Prism WebGL background — full spectrum, matching the React Bits demo.
       * The two overlay divs keep the text readable:
       *   1. A radial vignette dims the centre where copy lives.
       *   2. A bottom-fade merges the prism into the page background.
       */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10"
      >
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
            "radial-gradient(ellipse 80% 55% at 50% 35%, rgba(0,0,0,0.55) 0%, rgba(0,0,0,0.10) 60%, transparent 100%)",
        }}
      />

      {/* Bottom-fade blends the prism into the page background. */}
      <div
        aria-hidden
        className="hero-fade pointer-events-none absolute inset-0 -z-10"
      />

      <div className="mx-auto max-w-6xl px-4">
        <div className="mx-auto flex max-w-3xl flex-col items-center text-center">
          <span className="mb-6 inline-flex items-center gap-2 rounded-full border border-border bg-card/50 px-3 py-1 text-xs text-muted-foreground backdrop-blur">
            <span className="size-1.5 rounded-full bg-foreground/70" />
            AI-powered document intelligence
          </span>

          <SplitText
            text="Chat with your documents, intelligently."
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

          <p className="mt-6 max-w-xl text-balance text-base leading-relaxed text-muted-foreground sm:text-lg">
            Upload a PDF, ask questions, explore key ideas, and receive
            context-aware answers with source citations.
          </p>

          <div className="mt-9 flex flex-col items-center gap-3 sm:flex-row">
            <Button
              size="lg"
              nativeButton={false}
              render={<Link href="/chat" />}
              className="h-11 gap-2 px-6 text-sm"
              data-icon="inline-end"
            >
              Chat with a PDF
              <ArrowRight className="size-4" />
            </Button>
            <Button
              size="lg"
              variant="outline"
              nativeButton={false}
              render={<a href="#how-it-works" />}
              className="h-11 gap-2 px-6 text-sm"
              data-icon="inline-start"
            >
              <PlayCircle className="size-4" />
              See How It Works
            </Button>
          </div>
        </div>

        <div className="animate-float relative mx-auto mt-16 max-w-4xl sm:mt-20">
          <ProductMockup />
        </div>
      </div>
    </section>
  );
}
