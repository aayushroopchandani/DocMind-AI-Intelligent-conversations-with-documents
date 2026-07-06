import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Reveal } from "@/components/reveal";

export function FinalCta() {
  return (
    <section className="relative py-24 sm:py-32">
      <div className="mx-auto max-w-6xl px-4">
        <Reveal>
          <div className="glass relative overflow-hidden rounded-3xl px-6 py-16 text-center sm:px-16 sm:py-20">
            <div
              aria-hidden
              className="bg-grid pointer-events-none absolute inset-0 opacity-30"
            />
            <div className="relative">
              <h2 className="text-balance text-3xl font-semibold tracking-tight text-foreground sm:text-5xl">
                Turn your PDFs into conversations.
              </h2>
              <p className="mx-auto mt-5 max-w-lg text-balance text-muted-foreground">
                Start exploring any document with context-aware answers and
                verifiable citations.
              </p>
              <div className="mt-9 flex justify-center">
                <Button
                  size="lg"
                  nativeButton={false}
                  render={<Link href="/chat" />}
                  className="h-12 gap-2 px-8 text-sm"
                  data-icon="inline-end"
                >
                  Start Chatting
                  <ArrowRight className="size-4" />
                </Button>
              </div>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
