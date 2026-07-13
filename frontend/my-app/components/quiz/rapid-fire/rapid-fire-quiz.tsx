"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, CheckCircle2, Flame, XCircle, Zap } from "lucide-react";
import type { GradedResult, Quiz, QuizAnswer } from "@/lib/quiz/types";
import { emptyAnswer, gradeQuestion, isAnswerComplete } from "@/lib/quiz/grading";
import { Button } from "@/components/ui/button";
import { CircularTimer } from "@/components/quiz/shared/circular-timer";
import { QuestionRenderer } from "@/components/quiz/practice/question-renderer";
import { cn } from "@/lib/utils";
import { RapidFireIntro } from "./rapid-fire-intro";
import { RapidFireResult } from "./rapid-fire-result";

type Phase = "intro" | "playing" | "result";

const SECONDS_PER_QUESTION = 20;
const DURATION_MS = SECONDS_PER_QUESTION * 1000;
/** How long the resolved card lingers before the next one slides in. */
const REVEAL_MS = 950;

/** Formats that resolve on a single tap and can auto-advance. */
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
  const [results, setResults] = useState<GradedResult[]>(() =>
    questions.map(() => ({ correct: false })),
  );

  const [points, setPoints] = useState(0);
  const [streak, setStreak] = useState(0);
  const [bestStreak, setBestStreak] = useState(0);

  const [locked, setLocked] = useState(false);
  const [lastCorrect, setLastCorrect] = useState<boolean | null>(null);
  const [lastGain, setLastGain] = useState<number | null>(null);

  const current = questions[index];
  const currentAnswer = answers[index];
  const isLast = index === questions.length - 1;

  // Refs guard against double-locking (timer + tap racing) and stale reads.
  const lockedRef = useRef(false);
  const questionStartRef = useRef(0);
  const streakRef = useRef(0);
  const advanceTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );

  // Mark when each question becomes active so we can score answer speed.
  useEffect(() => {
    if (phase === "playing") questionStartRef.current = performance.now();
  }, [index, phase]);

  useEffect(() => {
    return () => clearTimeout(advanceTimer.current);
  }, []);

  const advance = useCallback(() => {
    lockedRef.current = false;
    setLocked(false);
    setLastCorrect(null);
    setLastGain(null);
    setIndex((i) => {
      if (i >= questions.length - 1) {
        setPhase("result");
        return i;
      }
      return i + 1;
    });
  }, [questions.length]);

  const lock = useCallback(
    (answer: QuizAnswer) => {
      if (lockedRef.current) return;
      lockedRef.current = true;

      const graded = gradeQuestion(current, answer);
      const elapsed = performance.now() - questionStartRef.current;
      const remainFraction = Math.max(0, 1 - elapsed / DURATION_MS);

      let gain = 0;
      if (graded.correct) {
        const base = 100;
        const speedBonus = Math.round(remainFraction * 100);
        const multiplier = 1 + streakRef.current * 0.1;
        gain = Math.round((base + speedBonus) * multiplier);
      }

      const nextStreak = graded.correct ? streakRef.current + 1 : 0;
      streakRef.current = nextStreak;

      setAnswers((prev) => {
        const next = [...prev];
        next[index] = answer;
        return next;
      });
      setResults((prev) => {
        const next = [...prev];
        next[index] = graded;
        return next;
      });
      setPoints((p) => p + gain);
      setStreak(nextStreak);
      setBestStreak((b) => Math.max(b, nextStreak));
      setLocked(true);
      setLastCorrect(graded.correct);
      setLastGain(gain);

      advanceTimer.current = setTimeout(advance, REVEAL_MS);
    },
    [current, index, advance],
  );

  const handleChange = useCallback(
    (answer: QuizAnswer) => {
      setAnswers((prev) => {
        const next = [...prev];
        next[index] = answer;
        return next;
      });
      if (!lockedRef.current && AUTO_LOCK_TYPES.has(current.type)) {
        lock(answer);
      }
    },
    [current.type, index, lock],
  );

  function restart() {
    clearTimeout(advanceTimer.current);
    lockedRef.current = false;
    streakRef.current = 0;
    setAnswers(questions.map(emptyAnswer));
    setResults(questions.map(() => ({ correct: false })));
    setIndex(0);
    setPoints(0);
    setStreak(0);
    setBestStreak(0);
    setLocked(false);
    setLastCorrect(null);
    setLastGain(null);
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

  if (phase === "result") {
    return (
      <Shell wide>
        <RapidFireResult
          quiz={quiz}
          answers={answers}
          results={results}
          points={points}
          bestStreak={bestStreak}
          onRestart={restart}
        />
      </Shell>
    );
  }

  const manualLock = !AUTO_LOCK_TYPES.has(current.type);

  return (
    <Shell>
      {/* HUD: timer · progress · score */}
      <div className="mb-6 flex items-center gap-4">
        <CircularTimer
          durationMs={DURATION_MS}
          running={phase === "playing" && !locked}
          resetKey={current.id}
          onExpire={() => lock(currentAnswer)}
        />
        <div className="flex-1">
          <div className="mb-1.5 flex items-center justify-between text-xs">
            <span className="font-medium text-muted-foreground">
              <span className="text-foreground">{index + 1}</span> /{" "}
              {questions.length}
            </span>
            <div className="flex items-center gap-3">
              <span
                key={streak}
                className={cn(
                  "inline-flex items-center gap-1 font-semibold",
                  streak > 0
                    ? "text-[color:var(--accent-amber)] animate-rf-streak"
                    : "text-muted-foreground",
                )}
              >
                <Flame className="size-3.5" />
                {streak}
              </span>
              <span className="relative inline-flex items-center gap-1 font-bold text-foreground tabular-nums">
                <Zap className="size-3.5 text-[color:var(--accent-cyan)]" />
                {points.toLocaleString()}
                {lastGain && lastGain > 0 ? (
                  <span
                    key={index}
                    className="animate-rf-float absolute -right-1 -top-4 text-xs font-bold text-[color:var(--quiz-correct)]"
                  >
                    +{lastGain}
                  </span>
                ) : null}
              </span>
            </div>
          </div>
          <div className="flex gap-1">
            {questions.map((q, i) => (
              <span
                key={q.id}
                className={cn(
                  "h-1 flex-1 rounded-full transition-colors",
                  i === index
                    ? "bg-[color:var(--accent-cyan)]"
                    : i < index
                      ? results[i].correct
                        ? "bg-[color:var(--quiz-correct)]"
                        : "bg-[color:var(--quiz-incorrect)]"
                      : "bg-muted",
                )}
              />
            ))}
          </div>
        </div>
      </div>

      {/* Card stack */}
      <div className="relative">
        <div
          aria-hidden
          className="rf-stack-card"
          style={{ transform: "translateY(16px) scale(0.94)", opacity: 0.4 }}
        />
        <div
          aria-hidden
          className="rf-stack-card"
          style={{ transform: "translateY(8px) scale(0.97)", opacity: 0.6 }}
        />

        <div
          key={current.id}
          className={cn(
            "rf-card animate-quiz-in relative overflow-hidden rounded-2xl border bg-card p-6 sm:p-7",
            locked && "rf-card--locked",
            locked && lastCorrect && "quiz-correct-surface",
            locked && lastCorrect === false && "quiz-incorrect-surface",
            !locked && "border-border",
          )}
        >
          <QuestionRenderer
            question={current}
            answer={currentAnswer}
            submitted={locked}
            graded={locked ? results[index] : undefined}
            onChange={handleChange}
          />

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

          {/* Verdict flash */}
          {locked && lastCorrect !== null ? (
            <div
              aria-hidden
              className={cn(
                "animate-rf-flash pointer-events-none absolute inset-0 flex items-center justify-center",
                lastCorrect
                  ? "bg-[color:var(--quiz-correct)]/15"
                  : "bg-[color:var(--quiz-incorrect)]/15",
              )}
            >
              {lastCorrect ? (
                <CheckCircle2 className="size-16 text-quiz-correct drop-shadow" />
              ) : (
                <XCircle className="size-16 text-quiz-incorrect drop-shadow" />
              )}
            </div>
          ) : null}
        </div>
      </div>

      {isLast && locked ? (
        <p className="mt-4 text-center text-xs text-muted-foreground">
          Tallying your score…
        </p>
      ) : null}
    </Shell>
  );
}

function Shell({
  children,
  wide,
}: {
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div
      className={cn(
        "mx-auto w-full px-4 py-10 sm:py-14",
        wide ? "max-w-2xl" : "max-w-xl",
      )}
    >
      {children}
    </div>
  );
}
