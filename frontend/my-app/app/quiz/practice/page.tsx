import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { PracticeQuiz } from "@/components/quiz/practice/practice-quiz";
import { SAMPLE_PRACTICE_QUIZ } from "@/lib/quiz/sample-quiz";

export const metadata: Metadata = {
  title: "Practice Quiz — DocMind",
};

/**
 * Standalone preview route for practice mode. Renders against a static sample
 * quiz so the UI can be developed and reviewed before backend wiring.
 */
export default function PracticeQuizPage() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-background">
      {/* Faint aurora wash — matches the chat workspace accents. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[420px] opacity-70"
        style={{
          background:
            "radial-gradient(60% 100% at 20% 0%, color-mix(in oklch, var(--accent-violet) 12%, transparent), transparent 60%), radial-gradient(60% 100% at 85% 0%, color-mix(in oklch, var(--accent-cyan) 10%, transparent), transparent 60%)",
        }}
      />

      <header className="relative z-10 mx-auto flex w-full max-w-2xl items-center justify-between px-4 pt-6">
        <Link
          href="/chat"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" />
          Back to chat
        </Link>
        <span className="text-xs font-medium text-muted-foreground">
          DocMind Quiz
        </span>
      </header>

      <div className="relative z-10">
        <PracticeQuiz quiz={SAMPLE_PRACTICE_QUIZ} />
      </div>
    </main>
  );
}
