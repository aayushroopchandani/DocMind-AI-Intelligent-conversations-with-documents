"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, LoaderCircle } from "lucide-react";
import type { Quiz, QuizAnswer, QuizAttempt } from "@/lib/quiz/types";
import { emptyAnswer, isAnswerComplete } from "@/lib/quiz/grading";
import {
  createQuizSubmissionId,
  submitQuizAttempt,
} from "@/lib/quiz/api";
import { Button } from "@/components/ui/button";
import { QuizProgress } from "@/components/quiz/shared/quiz-progress";
import { QuestionRenderer } from "./question-renderer";
import { QuizIntro } from "./quiz-intro";
import { QuizResult } from "./quiz-result";

type Phase = "intro" | "active" | "submitting" | "result";

interface PracticeQuizProps {
  quiz: Quiz;
}

/**
 * Practice-mode driver. Answers stay editable until the learner advances; the
 * complete attempt is evaluated once by the backend at the end.
 */
export function PracticeQuiz({ quiz }: PracticeQuizProps) {
  const questions = quiz.questions;

  const [phase, setPhase] = useState<Phase>("intro");
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<QuizAnswer[]>(() =>
    questions.map(emptyAnswer),
  );
  const [attempt, setAttempt] = useState<QuizAttempt | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const submissionIdRef = useRef(createQuizSubmissionId());
  const submissionStartedRef = useRef(false);
  const questionStartedAtRef = useRef(0);
  const timesRef = useRef<number[]>(questions.map(() => 0));

  const current = questions[index];
  const currentAnswer = answers[index];
  const isLast = index === questions.length - 1;

  const answeredCount = useMemo(
    () => answers.filter(isAnswerComplete).length,
    [answers],
  );

  useEffect(() => {
    if (phase === "active") questionStartedAtRef.current = performance.now();
  }, [index, phase]);

  function updateAnswer(answer: QuizAnswer) {
    if (submissionStartedRef.current) {
      submissionIdRef.current = createQuizSubmissionId();
      submissionStartedRef.current = false;
    }
    setAnswers((prev) => {
      const next = [...prev];
      next[index] = answer;
      return next;
    });
  }

  async function advance() {
    if (!submissionStartedRef.current) {
      timesRef.current[index] += Math.max(
        0,
        Math.round((performance.now() - questionStartedAtRef.current) / 1000),
      );
    }
    if (!isLast) {
      setIndex((i) => i + 1);
      return;
    }

    setPhase("submitting");
    setSubmitError(null);
    submissionStartedRef.current = true;
    try {
      const evaluated = await submitQuizAttempt(
        quiz,
        answers,
        submissionIdRef.current,
        timesRef.current,
      );
      setAttempt(evaluated);
      setPhase("result");
    } catch (cause) {
      setSubmitError(
        cause instanceof Error ? cause.message : "Unable to evaluate quiz",
      );
      setPhase("active");
    }
  }

  function restart() {
    submissionIdRef.current = createQuizSubmissionId();
    submissionStartedRef.current = false;
    timesRef.current = questions.map(() => 0);
    setAnswers(questions.map(emptyAnswer));
    setAttempt(null);
    setSubmitError(null);
    setIndex(0);
    setPhase("intro");
  }

  if (phase === "intro") {
    return (
      <Shell>
        <QuizIntro quiz={quiz} onStart={() => setPhase("active")} />
      </Shell>
    );
  }

  if (phase === "result" && attempt) {
    return (
      <Shell>
        <QuizResult
          quiz={quiz}
          attempt={attempt}
          onRestart={restart}
        />
      </Shell>
    );
  }

  if (phase === "submitting") {
    return (
      <Shell>
        <div className="animate-quiz-in rounded-2xl border border-border bg-card p-10 text-center">
          <LoaderCircle className="mx-auto size-7 animate-spin text-[color:var(--accent-violet)]" />
          <h2 className="mt-3 text-lg font-semibold text-foreground">
            Evaluating your attempt
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Your answers are being checked securely by the backend.
          </p>
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      <QuizProgress
        current={index + 1}
        total={questions.length}
        answered={answeredCount}
      />

      {/* `key` remounts the card each question so the entrance animation replays. */}
      <div
        key={current.id}
        className="animate-quiz-in mt-6 rounded-2xl border border-border bg-card p-6 sm:p-7"
      >
        <QuestionRenderer
          question={current}
          answer={currentAnswer}
          onChange={updateAnswer}
        />
      </div>

      {submitError ? (
        <p className="mt-4 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {submitError}
        </p>
      ) : null}

      <div className="mt-5 flex justify-end">
        <Button
          size="lg"
          onClick={advance}
          disabled={!isAnswerComplete(currentAnswer)}
          className="h-10 px-6 font-semibold"
        >
          {isLast ? "Submit for evaluation" : "Save & continue"}
          <ArrowRight className="size-4" data-icon="inline-end" />
        </Button>
      </div>
    </Shell>
  );
}

/** Centered, max-width column shared by every phase. */
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10 sm:py-14">{children}</div>
  );
}
