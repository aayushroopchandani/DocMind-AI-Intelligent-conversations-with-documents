import { ListChecks, Send, Timer, Zap } from "lucide-react";
import type { Quiz } from "@/lib/quiz/types";
import { Button } from "@/components/ui/button";
import SpotlightCard from "@/components/SpotlightCard";

interface RapidFireIntroProps {
  quiz: Quiz;
  secondsPerQuestion: number;
  onStart: () => void;
}

export function RapidFireIntro({
  quiz,
  secondsPerQuestion,
  onStart,
}: RapidFireIntroProps) {
  return (
    <SpotlightCard
      className="animate-quiz-in !border-border !bg-card p-8 sm:p-10"
      spotlightColor="rgba(255, 190, 90, 0.14)"
    >
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-[color:var(--accent-amber)]">
        <Zap className="size-4" />
        Rapid fire
      </div>

      <h1 className="mt-3 text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
        {quiz.target ?? "Beat the clock"}
      </h1>
      <p className="mt-2 max-w-lg text-sm leading-relaxed text-muted-foreground">
        {quiz.number_of_questions} questions, {secondsPerQuestion} seconds each.
        Lock in each answer before the timer expires. Your complete attempt is
        evaluated by the backend after the final question.
      </p>

      <ul className="mt-6 space-y-2.5">
        <Rule icon={Timer} tint="var(--accent-cyan)">
          <b className="font-semibold text-foreground">
            {secondsPerQuestion}s per question
          </b>{" "}
          — the clock auto-advances when it hits zero.
        </Rule>
        <Rule icon={ListChecks} tint="var(--accent-amber)">
          <b className="font-semibold text-foreground">One pass</b> — every
          locked answer moves immediately to the next question.
        </Rule>
        <Rule icon={Send} tint="var(--quiz-incorrect)">
          <b className="font-semibold text-foreground">Final evaluation</b> —
          correctness and partial credit appear only after submission.
        </Rule>
      </ul>

      <Button
        size="lg"
        onClick={onStart}
        className="mt-8 h-11 w-full text-sm font-semibold sm:w-auto sm:px-8"
      >
        <Zap className="size-4" data-icon="inline-start" />
        Start rapid fire
      </Button>
    </SpotlightCard>
  );
}

function Rule({
  icon: Icon,
  tint,
  children,
}: {
  icon: typeof Timer;
  tint: string;
  children: React.ReactNode;
}) {
  return (
    <li className="flex items-start gap-3 text-sm text-muted-foreground">
      <span
        className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg border"
        style={{
          borderColor: `color-mix(in oklch, ${tint} 35%, transparent)`,
          background: `color-mix(in oklch, ${tint} 12%, transparent)`,
          color: tint,
        }}
      >
        <Icon className="size-3.5" />
      </span>
      <span className="leading-relaxed">{children}</span>
    </li>
  );
}
