import type { Metadata } from "next";
import { QuizSetup } from "@/components/quiz/quiz-setup";

export const metadata: Metadata = {
  title: "Configure Quiz — DocMind",
};

export default function QuizSetupPage() {
  return <QuizSetup />;
}
