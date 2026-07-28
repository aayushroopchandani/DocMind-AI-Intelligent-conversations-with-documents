import {
  Braces,
  Database,
  FileSpreadsheet,
  FileText,
  History,
  LayoutDashboard,
  MessageSquareText,
  MousePointerClick,
  Quote,
  Sheet,
  Sigma,
  SlidersHorizontal,
  Sparkles,
  Table2,
  Upload,
  Wand2,
  type LucideIcon,
} from "lucide-react";

/**
 * All homepage copy in one place.
 *
 * Sections import from here rather than inlining strings, so the product
 * story can be rewritten without touching a single piece of layout.
 */

/** The six accents the homepage draws from, mapped to CSS custom properties. */
export type AccentKey =
  | "violet"
  | "cyan"
  | "amber"
  | "emerald"
  | "rose"
  | "blue";

export const ACCENT_VAR: Record<AccentKey, string> = {
  violet: "var(--accent-violet)",
  cyan: "var(--accent-cyan)",
  amber: "var(--accent-amber)",
  emerald: "var(--accent-emerald)",
  rose: "var(--accent-rose)",
  blue: "var(--accent-blue)",
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
/* Capabilities (bento grid)                                           */
/* ------------------------------------------------------------------ */

export interface Capability {
  icon: LucideIcon;
  title: string;
  description: string;
  accent: AccentKey;
  /** Tailwind column span at the `lg` breakpoint, out of 12. */
  span: string;
  /** Optional visual keyed by id in `capability-visuals.tsx`. */
  visual?: "sheet" | "chart" | "citation";
}

export const CAPABILITIES: readonly Capability[] = [
  {
    icon: Sigma,
    title: "It edits the spreadsheet, not just the chat",
    description:
      "The agent writes formulas, adds columns and worksheets, and reshapes ranges in place — results arrive as editable artifacts you can keep working in.",
    accent: "emerald",
    span: "lg:col-span-7",
    visual: "sheet",
  },
  {
    icon: LayoutDashboard,
    title: "Charts and dashboards on request",
    description:
      "From a one-line bar chart to a multi-series breakdown with trend overlays — described in plain language, rendered into the workspace.",
    accent: "cyan",
    span: "lg:col-span-5",
    visual: "chart",
  },
  {
    icon: Wand2,
    title: "Cleaning and profiling built in",
    description:
      "Missing values, duplicates, type drift, anomalies and outliers are surfaced before the analysis runs, not after you ship the number.",
    accent: "amber",
    span: "lg:col-span-4",
  },
  {
    icon: Braces,
    title: "Python in a secure sandbox",
    description:
      "pandas, Polars, NumPy, SciPy, scikit-learn and Matplotlib — generated, executed and returned as artifacts you can inspect.",
    accent: "violet",
    span: "lg:col-span-4",
  },
  {
    icon: MousePointerClick,
    title: "Preview every change before it lands",
    description:
      "Proposed edits arrive as a diff. Apply them, adjust them, or throw them away — nothing touches your data unattended.",
    accent: "blue",
    span: "lg:col-span-4",
  },
  {
    icon: Quote,
    title: "Every number traced to its source",
    description:
      "Answers cite the exact PDF page, sheet or cell range, and clicking a citation jumps you straight to the highlighted source.",
    accent: "rose",
    span: "lg:col-span-7",
    visual: "citation",
  },
  {
    icon: History,
    title: "Run history and undo",
    description:
      "Every operation the agent performs is recorded. Step back through the run and restore any earlier state of the analysis.",
    accent: "violet",
    span: "lg:col-span-5",
  },
] as const;

/* ------------------------------------------------------------------ */
/* Workflow                                                            */
/* ------------------------------------------------------------------ */

export interface WorkflowStep {
  step: string;
  title: string;
  description: string;
  icon: LucideIcon;
  accent: AccentKey;
}

export const WORKFLOW: readonly WorkflowStep[] = [
  {
    step: "01",
    icon: Upload,
    title: "Open your files",
    description:
      "Load PDFs, workbooks and CSVs into one workspace and view them side by side.",
    accent: "blue",
  },
  {
    step: "02",
    icon: MousePointerClick,
    title: "Select your context",
    description:
      "Highlight a PDF page, a table, or a range of cells — that selection becomes the agent's context.",
    accent: "cyan",
  },
  {
    step: "03",
    icon: MessageSquareText,
    title: "Ask the analyst",
    description:
      "Describe the analysis in plain language. The agent reads the data and plans the operation.",
    accent: "violet",
  },
  {
    step: "04",
    icon: SlidersHorizontal,
    title: "Preview and apply",
    description:
      "Inspect the proposed changes as a diff, then apply them to the live artifact.",
    accent: "amber",
  },
  {
    step: "05",
    icon: History,
    title: "Keep the trail",
    description:
      "The result opens in the workspace with citations attached, and the run is stored in history.",
    accent: "emerald",
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
