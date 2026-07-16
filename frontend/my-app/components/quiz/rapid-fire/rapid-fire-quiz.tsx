"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, LoaderCircle, RotateCcw, Zap } from "lucide-react";
import type { Quiz, QuizAnswer, QuizAttempt } from "@/lib/quiz/types";
import { emptyAnswer, isAnswerComplete } from "@/lib/quiz/grading";
import {
  createQuizSubmissionId,
  submitQuizAttempt,
} from "@/lib/quiz/api";
import { Button } from "@/components/ui/button";
import { CircularTimer } from "@/components/quiz/shared/circular-timer";
import { QuestionRenderer } from "@/components/quiz/practice/question-renderer";
import { cn } from "@/lib/utils";
import { RapidFireIntro } from "./rapid-fire-intro";
import { RapidFireResult } from "./rapid-fire-result";

type Phase = "intro" | "playing" | "submitting" | "result" | "error";

const SECONDS_PER_QUESTION = 20;
const DURATION_MS = SECONDS_PER_QUESTION * 1000;
const LOCK_DELAY_MS = 300;
const AUTO_LOCK_TYPES = new Set(["single_correct_mcq", "true_false"]);

interface RapidFireQuizProps {
  quiz: Quiz;
}

export function RapidFireQuiz({ quiz }: RapidFireQuizProps) {
  const questions = quiz.questions;
  const [phase, setPhase] = useState<Phase>("intro");
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<QuizAnswer[]>(() =>
    questions.map(emptyAnswer),
  );
  const [attempt, setAttempt] = useState<QuizAttempt | null>(null);
  const [locked, setLocked] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const answersRef = useRef(answers);
  const timesRef = useRef<number[]>(questions.map(() => 0));
  const lockedRef = useRef(false);
  const questionStartedAtRef = useRef(0);
  const submissionIdRef = useRef(createQuizSubmissionId());
  const advanceTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const current = questions[index];
  const currentAnswer = answers[index];
  const isLast = index === questions.length - 1;

  useEffect(() => {
    if (phase === "playing") questionStartedAtRef.current = performance.now();
  }, [index, phase]);

  useEffect(() => () => clearTimeout(advanceTimer.current), []);

  const finish = useCallback(
    async (finalAnswers: QuizAnswer[]) => {
      setPhase("submitting");
      setSubmitError(null);
      try {
        const evaluated = await submitQuizAttempt(
          quiz,
          finalAnswers,
          submissionIdRef.current,
          timesRef.current,
        );
        setAttempt(evaluated);
        setPhase("result");
      } catch (cause) {
        setSubmitError(
          cause instanceof Error ? cause.message : "Unable to evaluate quiz",
        );
        setPhase("error");
      }
    },
    [quiz],
  );

  const advance = useCallback(
    (finalAnswers: QuizAnswer[]) => {
      lockedRef.current = false;
      setLocked(false);
      if (index >= questions.length - 1) {
        void finish(finalAnswers);
      } else {
        setIndex((currentIndex) => currentIndex + 1);
      }
    },
    [finish, index, questions.length],
  );

  const lock = useCallback(
    (answer: QuizAnswer) => {
      if (lockedRef.current) return;
      lockedRef.current = true;

      const finalAnswers = [...answersRef.current];
      finalAnswers[index] = answer;
      answersRef.current = finalAnswers;
      timesRef.current[index] = Math.min(
        SECONDS_PER_QUESTION,
        Math.max(
          0,
          Math.round((performance.now() - questionStartedAtRef.current) / 1000),
        ),
      );
      setAnswers(finalAnswers);
      setLocked(true);
      advanceTimer.current = setTimeout(
        () => advance(finalAnswers),
        LOCK_DELAY_MS,
      );
    },
    [advance, index],
  );

  const handleChange = useCallback(
    (answer: QuizAnswer) => {
      if (lockedRef.current) return;
      const next = [...answersRef.current];
      next[index] = answer;
      answersRef.current = next;
      setAnswers(next);
      if (AUTO_LOCK_TYPES.has(current.type)) lock(answer);
    },
    [current.type, index, lock],
  );

  function restart() {
    clearTimeout(advanceTimer.current);
    const freshAnswers = questions.map(emptyAnswer);
    answersRef.current = freshAnswers;
    timesRef.current = questions.map(() => 0);
    lockedRef.current = false;
    submissionIdRef.current = createQuizSubmissionId();
    setAnswers(freshAnswers);
    setAttempt(null);
    setSubmitError(null);
    setIndex(0);
    setLocked(false);
    setPhase("intro");
  }

  if (phase === "intro") {
    return (
      <Shell>
        <RapidFireIntro
          quiz={quiz}
          secondsPerQuestion={SECONDS_PER_QUESTION}
          onStart={() => setPhase("playing")}
        />
      </Shell>
    );
  }

  if (phase === "submitting") {
    return (
      <Shell>
        <div className="animate-quiz-in rounded-2xl border border-border bg-card p-10 text-center">
          <LoaderCircle className="mx-auto size-7 animate-spin text-[color:var(--accent-amber)]" />
          <h2 className="mt-3 text-lg font-semibold text-foreground">Tallying your result</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            The backend is evaluating every response and any partial credit.
          </p>
        </div>
      </Shell>
    );
  }

  if (phase === "error") {
    return (
      <Shell>
        <div className="animate-quiz-in rounded-2xl border border-border bg-card p-8 text-center">
          <h2 className="text-lg font-semibold text-foreground">Evaluation failed</h2>
          <p className="mt-2 text-sm text-destructive">{submitError}</p>
          <div className="mt-5 flex justify-center gap-2">
            <Button variant="outline" onClick={restart}>
              <RotateCcw className="size-4" data-icon="inline-start" />
              Restart
            </Button>
            <Button onClick={() => void finish(answersRef.current)}>Try again</Button>
          </div>
        </div>
      </Shell>
    );
  }

  if (phase === "result" && attempt) {
    return (
      <Shell wide>
        <RapidFireResult
          answers={answers}
          attempt={attempt}
          onRestart={restart}
        />
      </Shell>
    );
  }

  const manualLock = !AUTO_LOCK_TYPES.has(current.type);

  return (
    <Shell>
      <div className="mb-6 flex items-center gap-4">
        <CircularTimer
          durationMs={DURATION_MS}
          running={phase === "playing" && !locked}
          resetKey={current.id}
          onExpire={() => lock(answersRef.current[index])}
        />
        <div className="flex-1">
          <div className="mb-1.5 flex items-center justify-between text-xs">
            <span className="font-medium text-muted-foreground">
              <span className="text-foreground">{index + 1}</span> / {questions.length}
            </span>
            <span className="inline-flex items-center gap-1 font-semibold text-muted-foreground">
              <Zap className="size-3.5 text-[color:var(--accent-amber)]" />
              Timed
            </span>
          </div>
          <div className="flex gap-1">
            {questions.map((question, questionIndex) => (
              <span
                key={question.id}
                className={cn(
                  "h-1 flex-1 rounded-full transition-colors",
                  questionIndex === index
                    ? "bg-[color:var(--accent-cyan)]"
                    : questionIndex < index
                      ? "bg-foreground/40"
                      : "bg-muted",
                )}
              />
            ))}
          </div>
        </div>
      </div>

      <div className="relative">
        <div aria-hidden className="rf-stack-card" style={{ transform: "translateY(16px) scale(0.94)", opacity: 0.4 }} />
        <div aria-hidden className="rf-stack-card" style={{ transform: "translateY(8px) scale(0.97)", opacity: 0.6 }} />

        <div
          key={current.id}
          className={cn(
            "rf-card animate-quiz-in relative overflow-hidden rounded-2xl border border-border bg-card p-6 sm:p-7",
            locked && "rf-card--locked",
          )}
        >
          <div className={cn(locked && "pointer-events-none opacity-75")}>
            <QuestionRenderer
              question={current}
              answer={currentAnswer}
              onChange={handleChange}
            />
          </div>

          {manualLock && !locked ? (
            <div className="mt-5 flex justify-end">
              <Button
                onClick={() => lock(currentAnswer)}
                disabled={!isAnswerComplete(currentAnswer)}
                className="h-10 px-6 font-semibold"
              >
                Lock in
                <ArrowRight className="size-4" data-icon="inline-end" />
              </Button>
            </div>
          ) : null}

          {!manualLock && !locked ? (
            <p className="mt-4 text-center text-xs text-muted-foreground">
              Tap an answer to lock it in
            </p>
          ) : null}

          {locked ? (
            <div className="animate-quiz-in pointer-events-none absolute inset-x-0 bottom-4 flex justify-center">
              <span className="rounded-full border border-border bg-background/90 px-3 py-1 text-xs font-semibold text-muted-foreground shadow-sm">
                Answer locked
              </span>
            </div>
          ) : null}
        </div>
      </div>

      {isLast && locked ? (
        <p className="mt-4 text-center text-xs text-muted-foreground">Submitting your attempt…</p>
      ) : null}
    </Shell>
  );
}

function Shell({ children, wide }: { children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={cn("mx-auto w-full px-4 py-10 sm:py-14", wide ? "max-w-2xl" : "max-w-xl")}>
      {children}
    </div>
  );
}
