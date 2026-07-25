import type { Metadata } from "next";
import { DataAnalysisShell } from "@/components/data-analysis/data-analysis-shell";

export const metadata: Metadata = {
  title: "Data Analysis — DocMind",
  description:
    "AI-powered analytics workspace: spreadsheets, an AI analyst and versioned agent runs.",
};

export default function DataAnalysisPage() {
  return <DataAnalysisShell />;
}
