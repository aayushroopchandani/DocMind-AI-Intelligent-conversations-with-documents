import {
  Braces,
  Database,
  FileSpreadsheet,
  FileText,
  History,
  LayoutDashboard,
  MessageSquareText,
  Sheet,
  SlidersHorizontal,
  Sparkles,
  Table2,
  type LucideIcon,
} from "lucide-react";

/**
 * All homepage copy in one place.
 *
 * Sections import from here rather than inlining strings, so the product story
 * can be rewritten without touching a single piece of layout. Anything with a
 * matching illustration references it by `id`; the id-to-component mapping
 * lives next to the illustrations, not here.
 */

/** Accents still used by the two-surface section. */
export type AccentKey = "violet" | "cyan";

export const ACCENT_VAR: Record<AccentKey, string> = {
  violet: "var(--accent-violet)",
  cyan: "var(--accent-cyan)",
};

/* ------------------------------------------------------------------ */
/* Hero                                                                */
/* ------------------------------------------------------------------ */

export const HERO_PROMPTS = [
  "Compare Q3 vs Q4 revenue and chart it",
  "Extract every table from this 90-page report",
  "Flag outliers in the returns column",
  "Pivot spend by vendor and month",
  "Clean the nulls, then add a growth formula",
] as const;

/* ------------------------------------------------------------------ */
/* Supported inputs                                                    */
/* ------------------------------------------------------------------ */

export interface FormatChip {
  icon: LucideIcon;
  label: string;
}

export const FORMATS: readonly FormatChip[] = [
  { icon: FileText, label: "PDF reports" },
  { icon: FileSpreadsheet, label: "XLSX / XLS" },
  { icon: Table2, label: "CSV" },
  { icon: Sheet, label: "Extracted PDF tables" },
  { icon: Database, label: "Databases" },
  { icon: Braces, label: "Python outputs" },
  { icon: LayoutDashboard, label: "Dashboards" },
  { icon: Sparkles, label: "Generated datasets" },
] as const;

/* ------------------------------------------------------------------ */
/* Journey — the three-step story                                      */
/* ------------------------------------------------------------------ */

export type JourneyId = "connect" | "analyse" | "share";

export interface JourneyStep {
  id: JourneyId;
  title: string;
  description: string;
}

export const JOURNEY: readonly JourneyStep[] = [
  {
    id: "connect",
    title: "Connect your data",
    description:
      "Bring in PDFs, workbooks, CSVs and extracted tables — your evidence and your numbers, side by side in one workspace.",
  },
  {
    id: "analyse",
    title: "The analyst does the work",
    description:
      "Ask in plain language. DocMind plans the steps, writes the Python and the formulas, and runs them against what you selected.",
  },
  {
    id: "share",
    title: "Keep results you can reuse",
    description:
      "Charts, cleaned sheets and cited findings stay in the workspace — repeatable, inspectable and ready to hand over.",
  },
] as const;

/* ------------------------------------------------------------------ */
/* Features — the illustrated grid                                     */
/* ------------------------------------------------------------------ */

export type FeatureId =
  | "charts"
  | "transform"
  | "code"
  | "context"
  | "sheet"
  | "citations";

export interface Feature {
  id: FeatureId;
  title: string;
  description: string;
}

export const FEATURES: readonly Feature[] = [
  {
    id: "charts",
    title: "Charts and dashboards on request",
    description:
      "Describe the breakdown you want and it is built in the workspace — from a single bar chart to a multi-series view with trend overlays.",
  },
  {
    id: "transform",
    title: "Messy data in, analysis-ready out",
    description:
      "Mixed formats, stray text and duplicate rows are cleaned, typed and reshaped into a table you can actually compute on.",
  },
  {
    id: "code",
    title: "See the code behind every result",
    description:
      "Each run shows the Python it executed, the formulas it wrote and the chart spec it built. Verify it, learn from it, or take it with you.",
  },
  {
    id: "context",
    title: "You decide what it reads",
    description:
      "Select the documents, sheets and cell ranges for a run. Everything outside that selection stays out of the answer.",
  },
  {
    id: "sheet",
    title: "It edits the spreadsheet, not just the chat",
    description:
      "Formulas, new columns and whole worksheets are written into the live artifact, so you keep working inside the result.",
  },
  {
    id: "citations",
    title: "Every number traced to its source",
    description:
      "Answers cite the exact page, sheet and cell range, and one click jumps you to the highlighted source.",
  },
] as const;

/** Secondary capabilities, listed rather than illustrated. */
export interface Highlight {
  icon: LucideIcon;
  title: string;
  description: string;
}

export const HIGHLIGHTS: readonly Highlight[] = [
  {
    icon: SlidersHorizontal,
    title: "Preview before apply",
    description: "Proposed edits arrive as a diff you accept or discard.",
  },
  {
    icon: History,
    title: "Run history and undo",
    description: "Step back through any run and restore an earlier state.",
  },
  {
    icon: Braces,
    title: "Sandboxed Python",
    description: "pandas, NumPy, SciPy and Matplotlib, executed in isolation.",
  },
  {
    icon: Table2,
    title: "Tables out of PDFs",
    description: "Structured extraction with units and types preserved.",
  },
] as const;

/* ------------------------------------------------------------------ */
/* Security                                                            */
/* ------------------------------------------------------------------ */

export type SecurityId = "ownership" | "encryption" | "sandbox";

export interface SecurityCard {
  id: SecurityId;
  title: string;
  description: string;
}

export const SECURITY: readonly SecurityCard[] = [
  {
    id: "ownership",
    title: "Your data stays yours",
    description:
      "Files, datasets and conversations stay inside your workspace and are never used to train models.",
  },
  {
    id: "encryption",
    title: "Encrypted in transit and at rest",
    description:
      "Documents travel over TLS and are stored encrypted, reachable only through signed, expiring URLs.",
  },
  {
    id: "sandbox",
    title: "Every run is sandboxed",
    description:
      "Generated code executes in an isolated environment scoped to the files you picked for that run — nothing else is reachable.",
  },
] as const;

/* ------------------------------------------------------------------ */
/* Entry points                                                        */
/* ------------------------------------------------------------------ */

export interface Surface {
  icon: LucideIcon;
  eyebrow: string;
  title: string;
  description: string;
  bullets: readonly string[];
  href: string;
  cta: string;
  accent: AccentKey;
  primary: boolean;
}

export const SURFACES: readonly Surface[] = [
  {
    icon: LayoutDashboard,
    eyebrow: "Analysis Workspace",
    title: "Open the full workspace",
    description:
      "A multi-document IDE for analysis: file explorer, spreadsheet and PDF panes, an AI Analyst sidebar, and run history.",
    bullets: [
      "Spreadsheets, PDFs and datasets in one place",
      "Formulas, charts and transforms as artifacts",
      "Diff preview, apply and undo",
    ],
    href: "/data-analysis",
    cta: "Open Analysis Workspace",
    accent: "cyan",
    primary: true,
  },
  {
    icon: MessageSquareText,
    eyebrow: "Document Chat",
    title: "Just need to read something?",
    description:
      "The focused reading mode: one document, one conversation, with citations that jump to the page they came from.",
    bullets: [
      "Side-by-side reader and chat",
      "Page-level citations",
      "Quick summaries and key ideas",
    ],
    href: "/chat",
    cta: "Chat with a PDF",
    accent: "violet",
    primary: false,
  },
] as const;
