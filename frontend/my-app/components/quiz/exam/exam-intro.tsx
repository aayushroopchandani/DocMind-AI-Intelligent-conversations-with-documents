import {
  AlertTriangle,
  Clock,
  Maximize,
  ListChecks,
  ShieldCheck,
} from "lucide-react";
import type { Quiz } from "@/lib/quiz/types";
import { Button } from "@/components/ui/button";

interface ExamIntroProps {
  quiz: Quiz;
  durationMinutes: number;
  onStart: () => void;
}

/**
 * Deliberately sober intro. Sets expectations for the timed, proctored run and
 * makes it explicit that leaving full-screen or the tab is recorded.
 */
export function ExamIntro({ quiz, durationMinutes, onStart }: ExamIntroProps) {
  return (
    <div className="animate-quiz-in rounded-2xl border border-border bg-card p-8 sm:p-10">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        <ShieldCheck className="size-4" />
        Exam mode · proctored
      </div>

      <h1 className="mt-3 text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
        {quiz.target ?? "Assessment"}
      </h1>
      <p className="mt-2 max-w-lg text-sm leading-relaxed text-muted-foreground">
        Answers are not graded until you submit. You can navigate freely between
        questions and flag any for review before submitting.
      </p>

      <div className="mt-6 grid grid-cols-2 gap-3">
        <Fact icon={ListChecks} label="Questions" value={String(quiz.number_of_questions)} />
        <Fact icon={Clock} label="Time limit" value={`${durationMinutes} min`} />
      </div>

      <div className="mt-6 rounded-xl border border-[color:var(--accent-amber)]/30 bg-[color:var(--accent-amber)]/[0.07] p-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-[color:var(--accent-amber)]">
          <AlertTriangle className="size-4" />
          Exam conditions
        </div>
        <ul className="mt-2.5 space-y-1.5 text-[13px] text-muted-foreground">
          <li className="flex items-start gap-2">
            <Maximize className="mt-0.5 size-3.5 shrink-0" />
            The exam runs in full-screen. Exiting full-screen pauses the exam.
          </li>
          <li className="flex items-start gap-2">
            <ShieldCheck className="mt-0.5 size-3.5 shrink-0" />
            Switching tabs or windows, and copy / right-click, are recorded as
            violations.
          </li>
          <li className="flex items-start gap-2">
            <Clock className="mt-0.5 size-3.5 shrink-0" />
            The exam auto-submits when the timer ends or after repeated
            violations.
          </li>
        </ul>
      </div>

      <Button
        size="lg"
        onClick={onStart}
        className="mt-8 h-11 w-full text-sm font-semibold sm:w-auto sm:px-8"
      >
        <Maximize className="size-4" data-icon="inline-start" />
        Start exam
      </Button>
    </div>
  );
}

function Fact({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Clock;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-background/60 p-3">
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        <Icon className="size-3.5" />
        {label}
      </div>
      <p className="mt-1.5 text-sm font-semibold text-foreground">{value}</p>
    </div>
  );
}
