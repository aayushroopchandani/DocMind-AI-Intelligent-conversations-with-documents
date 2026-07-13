import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft, ArrowRight, GraduationCap, Sparkles, Zap } from "lucide-react";

export const metadata: Metadata = {
  title: "Quiz — DocMind",
};

const MODES = [
  {
    href: "/quiz/practice",
    icon: Sparkles,
    tint: "var(--accent-violet)",
    name: "Practice",
    desc: "One question at a time with instant feedback and explanations. No timer — learn at your own pace.",
  },
  {
    href: "/quiz/rapid-fire",
    icon: Zap,
    tint: "var(--accent-amber)",
    name: "Rapid Fire",
    desc: "Beat the clock. Speed bonuses, streak multipliers and a gamified card run. Explanations at the end.",
  },
  {
    href: "/quiz/exam",
    icon: GraduationCap,
    tint: "var(--accent-cyan)",
    name: "Exam",
    desc: "Timed and proctored. Full-screen, no feedback until you submit, then a detailed review.",
  },
];

export default function QuizHubPage() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-background">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[420px] opacity-70"
        style={{
          background:
            "radial-gradient(60% 100% at 20% 0%, color-mix(in oklch, var(--accent-violet) 12%, transparent), transparent 60%), radial-gradient(60% 100% at 85% 0%, color-mix(in oklch, var(--accent-cyan) 10%, transparent), transparent 60%)",
        }}
      />

      <div className="relative z-10 mx-auto w-full max-w-2xl px-4 py-10 sm:py-16">
        <Link
          href="/chat"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" />
          Back to chat
        </Link>

        <h1 className="mt-6 text-3xl font-semibold tracking-tight text-foreground">
          Choose a quiz mode
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Same questions, three ways to test yourself.
        </p>

        <div className="mt-8 space-y-3">
          {MODES.map((mode) => (
            <Link
              key={mode.href}
              href={mode.href}
              className="group flex items-center gap-4 rounded-2xl border border-border bg-card p-5 transition-colors hover:border-foreground/25 hover:bg-accent"
            >
              <span
                className="flex size-11 shrink-0 items-center justify-center rounded-xl border"
                style={{
                  borderColor: `color-mix(in oklch, ${mode.tint} 35%, transparent)`,
                  background: `color-mix(in oklch, ${mode.tint} 12%, transparent)`,
                  color: mode.tint,
                }}
              >
                <mode.icon className="size-5" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-foreground">{mode.name}</p>
                <p className="mt-0.5 text-[13px] leading-snug text-muted-foreground">
                  {mode.desc}
                </p>
              </div>
              <ArrowRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
            </Link>
          ))}
        </div>
      </div>
    </main>
  );
}
