"use client";

import { useMemo, useState } from "react";
import { ArrowRight, CheckCheck } from "lucide-react";
import type { GradedResult, Quiz, QuizAnswer } from "@/lib/quiz/types";
import { emptyAnswer, gradeQuestion, isAnswerComplete } from "@/lib/quiz/grading";
import { Button } from "@/components/ui/button";
import { QuizProgress } from "@/components/quiz/shared/quiz-progress";
import { ExplanationPanel } from "@/components/quiz/shared/explanation-panel";
import { QuestionRenderer } from "./question-renderer";
import { QuizIntro } from "./quiz-intro";
import { QuizResult } from "./quiz-result";

type Phase = "intro" | "active" | "result";

interface PracticeQuizProps {
  quiz: Quiz;
}

/**
 * Practice-mode driver: presents one question at a time, checks the answer on
 * demand, reveals instant feedback + explanation, then advances. All state is
 * local; wiring to the backend only means swapping the `quiz` prop source.
 */
export function PracticeQuiz({ quiz }: PracticeQuizProps) {
  const questions = quiz.questions;

  const [phase, setPhase] = useState<Phase>("intro");
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<QuizAnswer[]>(() =>
    questions.map(emptyAnswer),
  );
  const [results, setResults] = useState<(GradedResult | null)[]>(() =>
    questions.map(() => null),
  );

  const current = questions[index];
  const currentAnswer = answers[index];
  const currentResult = results[index];
  const submitted = currentResult !== null;
  const isLast = index === questions.length - 1;

  const score = useMemo(
    () => results.filter((r) => r?.correct).length,
    [results],
  );
  const answeredCount = useMemo(
    () => results.filter((r) => r !== null).length,
    [results],
  );

  function updateAnswer(answer: QuizAnswer) {
    setAnswers((prev) => {
      const next = [...prev];
      next[index] = answer;
      return next;
    });
  }

  function checkAnswer() {
    const graded = gradeQuestion(current, currentAnswer);
    setResults((prev) => {
      const next = [...prev];
      next[index] = graded;
      return next;
    });
  }

  function advance() {
    if (isLast) setPhase("result");
    else setIndex((i) => i + 1);
  }

  function restart() {
    setAnswers(questions.map(emptyAnswer));
    setResults(questions.map(() => null));
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

  if (phase === "result") {
    return (
      <Shell>
        <QuizResult
          quiz={quiz}
          results={results.map((r) => r ?? { correct: false })}
          onRestart={restart}
        />
      </Shell>
    );
  }

  return (
    <Shell>
      <QuizProgress
        current={index + 1}
        total={questions.length}
        score={score}
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
          submitted={submitted}
          graded={currentResult ?? undefined}
          onChange={updateAnswer}
        />

        {submitted ? (
          <div className="mt-5">
            <ExplanationPanel
              correct={currentResult!.correct}
              explanation={current.short_explanation}
              citations={current.citations}
            />
          </div>
        ) : null}
      </div>

      <div className="mt-5 flex justify-end">
        {!submitted ? (
          <Button
            size="lg"
            onClick={checkAnswer}
            disabled={!isAnswerComplete(currentAnswer)}
            className="h-10 px-6 font-semibold"
          >
            <CheckCheck className="size-4" data-icon="inline-start" />
            Check answer
          </Button>
        ) : (
          <Button
            size="lg"
            onClick={advance}
            className="h-10 px-6 font-semibold"
          >
            {isLast ? "See results" : "Next question"}
            <ArrowRight className="size-4" data-icon="inline-end" />
          </Button>
        )}
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
