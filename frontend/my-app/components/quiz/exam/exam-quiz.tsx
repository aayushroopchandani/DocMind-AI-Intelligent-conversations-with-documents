"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Flag,
  LoaderCircle,
  RotateCcw,
  Send,
  ShieldCheck,
} from "lucide-react";
import type { Quiz, QuizAnswer, QuizAttempt } from "@/lib/quiz/types";
import { emptyAnswer, isAnswerComplete } from "@/lib/quiz/grading";
import {
  createQuizSubmissionId,
  submitQuizAttempt,
} from "@/lib/quiz/api";
import { useProctoring, type ViolationType } from "@/lib/quiz/use-proctoring";
import { Button } from "@/components/ui/button";
import { ExamTimer } from "@/components/quiz/shared/exam-timer";
import { QuestionRenderer } from "@/components/quiz/practice/question-renderer";
import { cn } from "@/lib/utils";
import { ExamIntro } from "./exam-intro";
import { ExamReview } from "./exam-review";
import { ExamSecurityOverlay } from "./exam-security-overlay";

type Phase = "intro" | "exam" | "submitting" | "submit_error" | "review";

const SECONDS_PER_QUESTION = 60;
const MAX_VIOLATIONS = 5;
/** Ignore focus churn in the first moment after entering full-screen. */
const VIOLATION_GRACE_MS = 1000;

interface ExamQuizProps {
  quiz: Quiz;
}

export function ExamQuiz({ quiz }: ExamQuizProps) {
  const questions = quiz.questions;
  const durationMs = questions.length * SECONDS_PER_QUESTION * 1000;
  const durationMinutes = Math.round(durationMs / 60000);

  const [phase, setPhase] = useState<Phase>("intro");
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<QuizAnswer[]>(() =>
    questions.map(emptyAnswer),
  );
  const [flagged, setFlagged] = useState<Set<number>>(new Set());
  const [confirming, setConfirming] = useState(false);

  const [violations, setViolations] = useState(0);
  const [lastViolation, setLastViolation] = useState<ViolationType | null>(null);
  const [blocked, setBlocked] = useState(false);

  const [attempt, setAttempt] = useState<QuizAttempt | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [autoSubmitted, setAutoSubmitted] = useState(false);

  const submittedRef = useRef(false);
  const violationsRef = useRef(0);
  const startedAtRef = useRef(0);
  const submissionIdRef = useRef(createQuizSubmissionId());
  const questionStartedAtRef = useRef(0);
  const timesRef = useRef<number[]>(questions.map(() => 0));

  const current = questions[index];

  const recordQuestionTime = useCallback(() => {
    if (!questionStartedAtRef.current) return;
    timesRef.current[index] += Math.max(
      0,
      Math.round((performance.now() - questionStartedAtRef.current) / 1000),
    );
    questionStartedAtRef.current = performance.now();
  }, [index]);

  useEffect(() => {
    if (phase === "exam") questionStartedAtRef.current = performance.now();
  }, [index, phase]);

  const submit = useCallback(
    async (auto: boolean) => {
      if (submittedRef.current) return;
      submittedRef.current = true;
      setAutoSubmitted(auto);
      setSubmitError(null);
      setBlocked(false);
      setPhase("submitting");
      if (phase === "exam") recordQuestionTime();
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});

      try {
        const evaluated = await submitQuizAttempt(
          quiz,
          answers,
          submissionIdRef.current,
          timesRef.current,
        );
        setAttempt(evaluated);
        setPhase("review");
      } catch (cause) {
        setSubmitError(
          cause instanceof Error ? cause.message : "Unable to evaluate exam",
        );
        setPhase("submit_error");
      }
    },
    [quiz, answers, phase, recordQuestionTime],
  );

  const handleViolation = useCallback(
    (type: ViolationType) => {
      if (submittedRef.current) return;
      if (performance.now() - startedAtRef.current < VIOLATION_GRACE_MS) return;
      const next = violationsRef.current + 1;
      violationsRef.current = next;
      setViolations(next);
      setLastViolation(type);
      if (next >= MAX_VIOLATIONS) void submit(true);
      else setBlocked(true);
    },
    [submit],
  );

  const { isFullscreen, enterFullscreen } = useProctoring({
    active: phase === "exam",
    onViolation: handleViolation,
  });

  function startExam() {
    // Full-screen is best-effort: some environments (sandboxed iframes) leave
    // the request pending, so we never block the exam start on it resolving.
    startedAtRef.current = performance.now();
    questionStartedAtRef.current = performance.now();
    void enterFullscreen();
    setPhase("exam");
  }

  function updateAnswer(answer: QuizAnswer) {
    setAnswers((prev) => {
      const next = [...prev];
      next[index] = answer;
      return next;
    });
  }

  function toggleFlag() {
    setFlagged((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  async function resume() {
    if (!isFullscreen) await enterFullscreen();
    setBlocked(false);
  }

  function retake() {
    submittedRef.current = false;
    violationsRef.current = 0;
    submissionIdRef.current = createQuizSubmissionId();
    questionStartedAtRef.current = 0;
    timesRef.current = questions.map(() => 0);
    setAnswers(questions.map(emptyAnswer));
    setFlagged(new Set());
    setAttempt(null);
    setSubmitError(null);
    setViolations(0);
    setLastViolation(null);
    setAutoSubmitted(false);
    setBlocked(false);
    setIndex(0);
    setPhase("intro");
  }

  if (phase === "intro") {
    return (
      <Shell>
        <ExamIntro
          quiz={quiz}
          durationMinutes={durationMinutes}
          onStart={startExam}
        />
      </Shell>
    );
  }

  if (phase === "review" && attempt) {
    return (
      <Shell>
        <ExamReview
          answers={answers}
          attempt={attempt}
          violations={violations}
          autoSubmitted={autoSubmitted}
          onRetake={retake}
        />
      </Shell>
    );
  }

  if (phase === "submitting") {
    return (
      <Shell>
        <div className="animate-quiz-in rounded-2xl border border-border bg-card p-10 text-center">
          <LoaderCircle className="mx-auto size-7 animate-spin text-[color:var(--accent-cyan)]" />
          <h2 className="mt-3 text-lg font-semibold text-foreground">
            Evaluating your exam
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Your submitted answers are being checked by the backend.
          </p>
        </div>
      </Shell>
    );
  }

  if (phase === "submit_error") {
    return (
      <Shell>
        <div className="animate-quiz-in rounded-2xl border border-border bg-card p-8 text-center">
          <h2 className="text-lg font-semibold text-foreground">
            Evaluation failed
          </h2>
          <p className="mt-2 text-sm text-destructive">{submitError}</p>
          <div className="mt-5 flex justify-center gap-2">
            <Button variant="outline" onClick={retake}>
              <RotateCcw className="size-4" data-icon="inline-start" />
              Retake
            </Button>
            <Button
              onClick={() => {
                submittedRef.current = false;
                void submit(autoSubmitted);
              }}
            >
              Try again
            </Button>
          </div>
        </div>
      </Shell>
    );
  }

  const answeredCount = answers.filter(isAnswerComplete).length;

  return (
    <div className="exam-noselect mx-auto w-full max-w-2xl px-4 py-8">
      {/* Status bar */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <ShieldCheck className="size-3.5 text-[color:var(--accent-cyan)]" />
          <span className="hidden sm:inline">Proctored</span>
          {violations > 0 ? (
            <span className="rounded-full border border-[color:var(--quiz-incorrect)]/30 bg-[color:var(--quiz-incorrect)]/10 px-2 py-0.5 font-medium text-quiz-incorrect">
              {violations} violation{violations === 1 ? "" : "s"}
            </span>
          ) : null}
        </div>
        <ExamTimer
          durationMs={durationMs}
          running={phase === "exam"}
          onExpire={() => void submit(true)}
        />
      </div>

      {/* Question */}
      <div className="mt-5 rounded-2xl border border-border bg-card p-6 sm:p-7">
        <div className="mb-4 flex items-center justify-between">
          <span className="text-xs font-medium text-muted-foreground">
            Question {index + 1} of {questions.length}
          </span>
          <button
            type="button"
            onClick={toggleFlag}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors",
              flagged.has(index)
                ? "border-[color:var(--accent-amber)]/40 bg-[color:var(--accent-amber)]/10 text-[color:var(--accent-amber)]"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            <Flag className="size-3.5" />
            {flagged.has(index) ? "Flagged" : "Flag"}
          </button>
        </div>

        <QuestionRenderer
          question={current}
          answer={answers[index]}
          onChange={updateAnswer}
        />
      </div>

      {/* Navigation */}
      <div className="mt-5 flex items-center justify-between gap-3">
        <Button
          variant="outline"
          className="h-9"
          disabled={index === 0}
          onClick={() => {
            recordQuestionTime();
            setIndex((i) => Math.max(0, i - 1));
          }}
        >
          <ChevronLeft className="size-4" data-icon="inline-start" />
          Prev
        </Button>

        {index === questions.length - 1 ? (
          <Button
            className="h-9 font-semibold"
            onClick={() => setConfirming(true)}
          >
            <Send className="size-4" data-icon="inline-start" />
            Submit exam
          </Button>
        ) : (
          <Button
            variant="outline"
            className="h-9"
            onClick={() => {
              recordQuestionTime();
              setIndex((i) => Math.min(questions.length - 1, i + 1));
            }}
          >
            Next
            <ChevronRight className="size-4" data-icon="inline-end" />
          </Button>
        )}
      </div>

      {/* Question palette */}
      <div className="mt-6 rounded-xl border border-border bg-card p-4">
        <div className="mb-2.5 flex items-center justify-between text-[11px] uppercase tracking-wide text-muted-foreground">
          <span>Questions</span>
          <span>
            {answeredCount}/{questions.length} answered
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          {questions.map((q, i) => {
            const answered = isAnswerComplete(answers[i]);
            return (
              <button
                key={q.id}
                type="button"
                onClick={() => {
                  if (i === index) return;
                  recordQuestionTime();
                  setIndex(i);
                }}
                className={cn(
                  "relative size-9 rounded-lg border text-xs font-semibold transition-colors",
                  i === index
                    ? "border-[color:var(--accent-cyan)] bg-[color:var(--accent-cyan)]/15 text-foreground"
                    : answered
                      ? "border-transparent bg-secondary text-foreground"
                      : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {i + 1}
                {flagged.has(i) ? (
                  <Flag className="absolute -right-1 -top-1 size-3 text-[color:var(--accent-amber)]" />
                ) : null}
              </button>
            );
          })}
        </div>
      </div>

      {/* Submit confirmation */}
      {confirming ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="submit-exam-title"
          className="fixed inset-0 z-40 flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm"
        >
          <div className="max-w-sm rounded-2xl border border-border bg-card p-6 text-center">
            <h2
              id="submit-exam-title"
              className="text-lg font-semibold text-foreground"
            >
              Submit exam?
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              You&apos;ve answered {answeredCount} of {questions.length}{" "}
              questions. You can&apos;t change answers after submitting.
            </p>
            <div className="mt-5 flex gap-2">
              <Button
                autoFocus
                variant="outline"
                className="h-9 flex-1"
                onClick={() => setConfirming(false)}
              >
                Keep going
              </Button>
              <Button
                className="h-9 flex-1 font-semibold"
                onClick={() => {
                  setConfirming(false);
                  void submit(false);
                }}
              >
                Submit
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {blocked ? (
        <ExamSecurityOverlay
          violationType={lastViolation}
          violations={violations}
          maxViolations={MAX_VIOLATIONS}
          onResume={resume}
        />
      ) : null}
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10 sm:py-14">
      {children}
    </div>
  );
}
