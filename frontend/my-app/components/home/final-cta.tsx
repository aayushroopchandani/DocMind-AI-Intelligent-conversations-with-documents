import Link from "next/link";
import { ArrowRight, MessageSquareText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Reveal } from "@/components/reveal";

export function FinalCta() {
  return (
    <section className="relative pb-24 sm:pb-32">
      <div className="mx-auto max-w-6xl px-4">
        <Reveal>
          <div className="glass section-aurora relative overflow-hidden rounded-3xl px-6 py-16 text-center sm:px-16 sm:py-20">
            <div
              aria-hidden
              className="bg-grid pointer-events-none absolute inset-0 opacity-25"
            />
            <div className="relative">
              <h2 className="dm-headline text-balance text-3xl font-semibold tracking-tight sm:text-5xl">
                Stop reading reports. Start querying them.
              </h2>
              <p className="mx-auto mt-5 max-w-xl text-balance text-muted-foreground">
                Bring your PDFs, workbooks and datasets into one workspace and
                let the analyst do the work — with every result traceable back
                to its source.
              </p>
              <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
                <Button
                  size="lg"
                  nativeButton={false}
                  render={<Link href="/data-analysis" />}
                  className="h-12 gap-2 px-8 text-sm"
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
                  className="h-12 gap-2 px-8 text-sm"
                  data-icon="inline-start"
                >
                  <MessageSquareText className="size-4" />
                  Chat with a PDF
                </Button>
              </div>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
