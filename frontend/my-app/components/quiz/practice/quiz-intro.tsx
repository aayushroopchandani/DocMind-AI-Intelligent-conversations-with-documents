import {
  BookOpen,
  GraduationCap,
  ListChecks,
  Play,
  Sparkles,
  Target,
} from "lucide-react";
import type { Quiz, QuizQuestionFormat } from "@/lib/quiz/types";
import { Button } from "@/components/ui/button";
import SpotlightCard from "@/components/SpotlightCard";

interface QuizIntroProps {
  quiz: Quiz;
  onStart: () => void;
}

const FORMAT_LABELS: Record<QuizQuestionFormat, string> = {
  single_correct_mcq: "Single choice",
  multiple_correct_mcq: "Multiple choice",
  true_false: "True / False",
  fill_in_the_blank: "Fill in the blank",
  match_the_following: "Match the following",
};

const DIFFICULTY_TINT: Record<Quiz["difficulty"], string> = {
  easy: "text-[color:var(--quiz-correct)] border-[color:var(--quiz-correct)]/30 bg-[color:var(--quiz-correct)]/10",
  medium:
    "text-[color:var(--accent-amber)] border-[color:var(--accent-amber)]/30 bg-[color:var(--accent-amber)]/10",
  hard: "text-[color:var(--quiz-incorrect)] border-[color:var(--quiz-incorrect)]/30 bg-[color:var(--quiz-incorrect)]/10",
};

export function QuizIntro({ quiz, onStart }: QuizIntroProps) {
  return (
    <SpotlightCard
      className="animate-quiz-in !border-border !bg-card p-8 sm:p-10"
      spotlightColor="rgba(180, 130, 255, 0.12)"
    >
      <div className="flex items-center gap-2 text-xs font-medium text-[color:var(--accent-violet)]">
        <Sparkles className="size-3.5" />
        Practice mode
      </div>

      <h1 className="mt-3 text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
        {quiz.target ?? "Document quiz"}
      </h1>
      <p className="mt-2 max-w-lg text-sm leading-relaxed text-muted-foreground">
        One question at a time with instant feedback and explanations. Take your
        time — there&apos;s no timer here.
      </p>

      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <MetaTile
          icon={ListChecks}
          label="Questions"
          value={String(quiz.number_of_questions)}
        />
        <MetaTile
          icon={Target}
          label="Scope"
          value={quiz.quiz_scope.replace(/_/g, " ")}
        />
        <div className="rounded-xl border border-border bg-background/60 p-3">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            <GraduationCap className="size-3.5" />
            Difficulty
          </div>
          <span
            className={`mt-1.5 inline-block rounded-full border px-2 py-0.5 text-xs font-semibold capitalize ${DIFFICULTY_TINT[quiz.difficulty]}`}
          >
            {quiz.difficulty}
          </span>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-1.5">
        <span className="flex items-center gap-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          <BookOpen className="size-3" />
          Formats
        </span>
        {quiz.question_formats.map((format) => (
          <span
            key={format}
            className="rounded-full border border-border bg-muted/50 px-2 py-0.5 text-[11px] text-foreground/80"
          >
            {FORMAT_LABELS[format]}
          </span>
        ))}
      </div>

      <Button
        size="lg"
        onClick={onStart}
        className="mt-8 h-11 w-full text-sm font-semibold sm:w-auto sm:px-8"
      >
        <Play className="size-4" data-icon="inline-start" />
        Start practice
      </Button>
    </SpotlightCard>
  );
}

function MetaTile({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Target;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-background/60 p-3">
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        <Icon className="size-3.5" />
        {label}
      </div>
      <p className="mt-1.5 text-sm font-semibold capitalize text-foreground">
        {value}
      </p>
    </div>
  );
}
