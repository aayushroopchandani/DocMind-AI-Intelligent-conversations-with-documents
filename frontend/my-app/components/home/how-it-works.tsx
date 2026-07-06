import { Upload, MessagesSquare, BadgeCheck } from "lucide-react";
import { Reveal } from "@/components/reveal";

const STEPS = [
  {
    icon: Upload,
    step: "01",
    title: "Upload your PDF",
    description:
      "Drag and drop any PDF. It stays in your browser — nothing is stored until you decide to.",
  },
  {
    icon: MessagesSquare,
    step: "02",
    title: "Ask questions",
    description:
      "Ask anything in plain language, or start from a suggested prompt to explore the document.",
  },
  {
    icon: BadgeCheck,
    step: "03",
    title: "Receive answers with sources",
    description:
      "Get clear, context-aware answers — each backed by citations you can jump to instantly.",
  },
] as const;

export function HowItWorks() {
  return (
    <section
      id="how-it-works"
      className="relative scroll-mt-24 border-y border-border bg-card/30 py-24 sm:py-32"
    >
      <div className="mx-auto max-w-6xl px-4">
        <Reveal className="mx-auto max-w-2xl text-center">
          <h2 className="text-balance text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
            How it works
          </h2>
          <p className="mt-4 text-balance text-muted-foreground">
            From upload to answer in three simple steps.
          </p>
        </Reveal>

        <div className="mt-16 grid grid-cols-1 gap-6 md:grid-cols-3">
          {STEPS.map((s, i) => (
            <Reveal key={s.step} delay={i * 0.1}>
              <div className="relative h-full rounded-2xl border border-border bg-background/50 p-7">
                <span className="mb-6 block font-mono text-sm text-muted-foreground">
                  {s.step}
                </span>
                <div className="mb-4 inline-flex size-11 items-center justify-center rounded-xl border border-border bg-card text-foreground">
                  <s.icon className="size-5" />
                </div>
                <h3 className="mb-2 text-lg font-semibold tracking-tight text-foreground">
                  {s.title}
                </h3>
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {s.description}
                </p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
