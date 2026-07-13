import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { ExamQuiz } from "@/components/quiz/exam/exam-quiz";
import { SAMPLE_EXAM_QUIZ } from "@/lib/quiz/sample-quiz";

export const metadata: Metadata = {
  title: "Exam — DocMind",
};

export default function ExamQuizPage() {
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

      <ExamQuiz quiz={SAMPLE_EXAM_QUIZ} />
    </main>
  );
}
