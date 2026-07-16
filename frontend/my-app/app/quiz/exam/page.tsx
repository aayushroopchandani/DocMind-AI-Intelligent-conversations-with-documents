import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { GeneratedQuizLoader } from "@/components/quiz/generated-quiz-loader";

export const metadata: Metadata = {
  title: "Exam — DocMind",
};

export default async function ExamQuizPage({
  searchParams,
}: {
  searchParams: Promise<{ quizId?: string | string[] }>;
}) {
  const { quizId } = await searchParams;
  const persistedQuizId = Array.isArray(quizId) ? quizId[0] : quizId;

  return (
    <main className="relative min-h-screen bg-background">
      <header className="relative z-10 mx-auto flex w-full max-w-2xl items-center justify-between px-4 pt-6">
        <Link
          href="/chat"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" />
          Back to chat
        </Link>
        <span className="text-xs font-medium text-muted-foreground">
          DocMind Exam
        </span>
      </header>

      <GeneratedQuizLoader quizId={persistedQuizId} mode="exam_mode" />
    </main>
  );
}
