"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertCircle, ArrowLeft, LoaderCircle } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { ExamQuiz } from "@/components/quiz/exam/exam-quiz";
import { PracticeQuiz } from "@/components/quiz/practice/practice-quiz";
import { RapidFireQuiz } from "@/components/quiz/rapid-fire/rapid-fire-quiz";
import { getGeneratedQuiz } from "@/lib/quiz/api";
import { quizHref } from "@/lib/quiz-session";
import type { Quiz, QuizMode } from "@/lib/quiz/types";

interface GeneratedQuizLoaderProps {
  quizId?: string;
  mode: QuizMode;
}

export function GeneratedQuizLoader({ quizId, mode }: GeneratedQuizLoaderProps) {
  return (
    <GeneratedQuizLoaderForId
      key={`${quizId ?? "missing"}:${mode}`}
      quizId={quizId}
      mode={mode}
    />
  );
}

function GeneratedQuizLoaderForId({ quizId, mode }: GeneratedQuizLoaderProps) {
  const router = useRouter();
  const [quiz, setQuiz] = useState<Quiz | null>(null);
  const [error, setError] = useState<string | null>(
    quizId ? null : "No quiz id was provided.",
  );

  useEffect(() => {
    if (!quizId) return;
    const controller = new AbortController();

    getGeneratedQuiz(quizId, controller.signal)
      .then(setQuiz)
      .catch((cause) => {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause.message : "Unable to load quiz");
      });

    return () => controller.abort();
  }, [quizId]);

  useEffect(() => {
    if (quiz?.id && quiz.mode && quiz.mode !== mode) {
      router.replace(quizHref(quiz.id, quiz.mode));
    }
  }, [mode, quiz, router]);

  if (error) return <LoadError message={error} />;
  if (!quiz) return <LoadingQuiz />;
  const questionIds = new Set(quiz.questions.map((question) => question.id));
  if (
    quiz.questions.length === 0 ||
    quiz.questions.length !== quiz.number_of_questions ||
    questionIds.size !== quiz.questions.length
  ) {
    return <LoadError message="This saved quiz is incomplete and cannot be attempted." />;
  }
  if (quiz.mode && quiz.mode !== mode) return <LoadingQuiz />;

  const effectiveMode = quiz.mode ?? mode;
  if (effectiveMode === "rapid_fire") return <RapidFireQuiz quiz={quiz} />;
  if (effectiveMode === "exam_mode") return <ExamQuiz quiz={quiz} />;
  return <PracticeQuiz quiz={quiz} />;
}

function LoadingQuiz() {
  return (
    <div className="mx-auto flex min-h-[60vh] max-w-md items-center justify-center px-4 text-center">
      <div className="animate-quiz-in">
        <LoaderCircle className="mx-auto size-7 animate-spin text-[color:var(--accent-violet)]" />
        <p className="mt-3 text-sm font-medium text-foreground">Loading your quiz…</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Fetching the saved questions securely.
        </p>
      </div>
    </div>
  );
}

function LoadError({ message }: { message: string }) {
  return (
    <div className="mx-auto flex min-h-[60vh] max-w-md items-center justify-center px-4">
      <div className="animate-quiz-in rounded-2xl border border-border bg-card p-7 text-center">
        <AlertCircle className="mx-auto size-7 text-destructive" />
        <h1 className="mt-3 text-lg font-semibold text-foreground">
          We couldn&apos;t open this quiz
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">{message}</p>
        <Link
          href="/chat"
          className={buttonVariants({ variant: "outline", className: "mt-5" })}
        >
          <ArrowLeft className="size-4" data-icon="inline-start" />
          Back to chat
        </Link>
      </div>
    </div>
  );
}
