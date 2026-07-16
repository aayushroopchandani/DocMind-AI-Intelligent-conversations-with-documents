"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  Check,
  GraduationCap,
  LoaderCircle,
  Sparkles,
  WandSparkles,
  Zap,
} from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  clearPendingQuizGeneration,
  PENDING_QUIZ_SESSION_KEY,
  quizHref,
  readPendingQuizGeneration,
  type PendingQuizGeneration,
} from "@/lib/quiz-session";
import { generateConfiguredQuiz } from "@/lib/quiz/generation";
import type { QuizMode, QuizQuestionFormat } from "@/lib/quiz/types";

const MODE_OPTIONS: {
  value: QuizMode;
  label: string;
  description: string;
  icon: typeof Sparkles;
}[] = [
  {
    value: "practice",
    label: "Practice",
    description: "Learn at your own pace, then review the evaluated attempt.",
    icon: Sparkles,
  },
  {
    value: "rapid_fire",
    label: "Rapid fire",
    description: "A short timer on every question keeps the pace moving.",
    icon: Zap,
  },
  {
    value: "exam_mode",
    label: "Exam",
    description: "A timed, focused assessment with review after submission.",
    icon: GraduationCap,
  },
];

const FORMAT_OPTIONS: { value: QuizQuestionFormat; label: string }[] = [
  { value: "single_correct_mcq", label: "Single choice" },
  { value: "multiple_correct_mcq", label: "Multiple choice" },
  { value: "true_false", label: "True / False" },
  { value: "fill_in_the_blank", label: "Fill in the blank" },
  { value: "match_the_following", label: "Match the following" },
];

const QUESTION_COUNTS = [5, 10, 15, 20] as const;

export function QuizSetup() {
  const router = useRouter();
  const storedValue = useSyncExternalStore(
    subscribeToStorage,
    readStoredValue,
    () => undefined,
  );
  const pending = useMemo<PendingQuizGeneration | null | undefined>(
    () =>
      storedValue === undefined
        ? undefined
        : storedValue === null
          ? null
          : readPendingQuizGeneration(),
    [storedValue],
  );
  const [modeOverride, setModeOverride] = useState<QuizMode | null>(null);
  const [questionCountOverride, setQuestionCountOverride] = useState<number | null>(null);
  const [formatsOverride, setFormatsOverride] = useState<QuizQuestionFormat[] | null>(null);
  const [generating, setGenerating] = useState(false);
  const [status, setStatus] = useState("Preparing your quiz…");
  const [error, setError] = useState<string | null>(null);
  const generationControllerRef = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      generationControllerRef.current?.abort();
    },
    [],
  );

  const mode = modeOverride ?? pending?.intent.mode ?? null;
  const questionCount =
    questionCountOverride ?? pending?.intent.number_of_questions ?? 5;
  const defaultFormats = useMemo<QuizQuestionFormat[]>(
    () =>
      pending?.intent.question_formats?.length
        ? pending.intent.question_formats
        : ["single_correct_mcq"],
    [pending],
  );
  const formats = formatsOverride ?? defaultFormats;

  const missing = useMemo(
    () => new Set(pending?.missingFields ?? []),
    [pending],
  );

  function toggleFormat(format: QuizQuestionFormat) {
    setFormatsOverride((override) => {
      const current = override ?? defaultFormats;
      return current.includes(format)
        ? current.filter((item) => item !== format)
        : [...current, format];
    });
  }

  async function generate() {
    if (!pending || !mode || formats.length === 0 || generating) return;
    setGenerating(true);
    setError(null);
    const controller = new AbortController();
    generationControllerRef.current = controller;

    try {
      const quiz = await generateConfiguredQuiz(
        {
          chatId: pending.chatId,
          question: pending.question,
          documentIds: pending.documentIds,
          messageId: pending.messageId,
          sourceMessageId: pending.sourceMessageId,
          mode,
          numberOfQuestions: questionCount,
          questionFormats: formats,
        },
        (message) => setStatus(message),
        controller.signal,
      );
      if (controller.signal.aborted) return;
      clearPendingQuizGeneration();
      router.replace(quizHref(quiz.id!, quiz.mode ?? mode));
    } catch (cause) {
      if (controller.signal.aborted) return;
      setError(cause instanceof Error ? cause.message : "Quiz generation failed");
      setGenerating(false);
    } finally {
      if (generationControllerRef.current === controller) {
        generationControllerRef.current = null;
      }
    }
  }

  if (pending === undefined) {
    return <QuizSetupState message="Loading quiz settings…" spinning />;
  }

  if (pending === null) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="animate-quiz-in max-w-md rounded-2xl border border-border bg-card p-8 text-center">
          <WandSparkles className="mx-auto size-8 text-[color:var(--accent-violet)]" />
          <h1 className="mt-4 text-xl font-semibold text-foreground">
            No quiz is waiting to be configured
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            Ask for a quiz in a document chat. If any settings are missing,
            DocMind will bring you back here to choose them.
          </p>
          <Link href="/chat" className={buttonVariants({ className: "mt-6" })}>
            <ArrowLeft className="size-4" data-icon="inline-start" />
            Back to chat
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="relative min-h-screen overflow-hidden bg-background">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[420px] opacity-70"
        style={{
          background:
            "radial-gradient(60% 100% at 20% 0%, color-mix(in oklch, var(--accent-violet) 12%, transparent), transparent 60%), radial-gradient(60% 100% at 85% 0%, color-mix(in oklch, var(--accent-cyan) 10%, transparent), transparent 60%)",
        }}
      />

      <div className="relative z-10 mx-auto w-full max-w-2xl px-4 py-10 sm:py-14">
        <Link
          href={`/chat/${pending.chatId}`}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" />
          Back to chat
        </Link>

        <div className="animate-quiz-in mt-6 rounded-2xl border border-border bg-card p-6 sm:p-8">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-[color:var(--accent-violet)]">
            <WandSparkles className="size-4" />
            Complete quiz setup
          </div>
          <h1 className="mt-3 text-2xl font-semibold tracking-tight text-foreground">
            A few choices before we generate it
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            We only ask for details that weren&apos;t included in your message.
          </p>

          <div className="mt-7 space-y-7">
            {missing.has("mode") ? (
              <Fieldset legend="Quiz mode">
                <div className="grid gap-2 sm:grid-cols-3">
                  {MODE_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={mode === option.value}
                      onClick={() => setModeOverride(option.value)}
                      className={cn(
                        "rounded-xl border p-4 text-left transition-colors",
                        mode === option.value
                          ? "border-[color:var(--accent-violet)]/60 bg-[color:var(--accent-violet)]/10"
                          : "border-border bg-background/50 hover:border-foreground/25",
                      )}
                    >
                      <option.icon className="size-4 text-[color:var(--accent-violet)]" />
                      <span className="mt-2 block text-sm font-semibold text-foreground">
                        {option.label}
                      </span>
                      <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
                        {option.description}
                      </span>
                    </button>
                  ))}
                </div>
              </Fieldset>
            ) : null}

            {missing.has("number_of_questions") ? (
              <Fieldset legend="Number of questions">
                <div className="grid grid-cols-4 gap-2">
                  {QUESTION_COUNTS.map((count) => (
                    <button
                      key={count}
                      type="button"
                      aria-pressed={questionCount === count}
                      onClick={() => setQuestionCountOverride(count)}
                      className={cn(
                        "rounded-xl border py-3 text-sm font-semibold transition-colors",
                        questionCount === count
                          ? "border-[color:var(--accent-cyan)]/60 bg-[color:var(--accent-cyan)]/10 text-foreground"
                          : "border-border bg-background/50 text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {count}
                    </button>
                  ))}
                </div>
              </Fieldset>
            ) : null}

            {missing.has("question_formats") ? (
              <Fieldset legend="Question formats">
                <p className="mb-3 text-xs text-muted-foreground">
                  Select one or more formats. Single choice is selected by default.
                </p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {FORMAT_OPTIONS.map((option) => {
                    const selected = formats.includes(option.value);
                    return (
                      <button
                        key={option.value}
                        type="button"
                        aria-pressed={selected}
                        onClick={() => toggleFormat(option.value)}
                        className={cn(
                          "flex items-center gap-3 rounded-xl border px-3 py-3 text-left text-sm transition-colors",
                          selected
                            ? "border-[color:var(--accent-violet)]/50 bg-[color:var(--accent-violet)]/10 text-foreground"
                            : "border-border bg-background/50 text-muted-foreground hover:text-foreground",
                        )}
                      >
                        <span
                          className={cn(
                            "flex size-5 items-center justify-center rounded-md border",
                            selected
                              ? "border-[color:var(--accent-violet)] bg-[color:var(--accent-violet)] text-white"
                              : "border-border",
                          )}
                        >
                          {selected ? <Check className="size-3.5" /> : null}
                        </span>
                        {option.label}
                      </button>
                    );
                  })}
                </div>
              </Fieldset>
            ) : null}
          </div>

          {error ? (
            <p className="mt-5 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </p>
          ) : null}

          <Button
            size="lg"
            onClick={generate}
            disabled={!mode || formats.length === 0 || generating}
            className="mt-7 h-11 w-full font-semibold sm:w-auto sm:px-8"
          >
            <WandSparkles className="size-4" data-icon="inline-start" />
            Generate quiz
          </Button>
        </div>
      </div>

      {generating ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/75 p-4 backdrop-blur-sm">
          <div className="animate-quiz-in rounded-2xl border border-border bg-card px-8 py-7 text-center shadow-xl">
            <LoaderCircle className="mx-auto size-7 animate-spin text-[color:var(--accent-violet)]" />
            <p className="mt-3 text-sm font-semibold text-foreground">
              Switching to quiz mode
            </p>
            <p className="mt-1 max-w-xs text-xs text-muted-foreground">{status}</p>
          </div>
        </div>
      ) : null}
    </main>
  );
}

function Fieldset({ legend, children }: { legend: string; children: React.ReactNode }) {
  return (
    <fieldset>
      <legend className="mb-3 text-sm font-semibold text-foreground">{legend}</legend>
      {children}
    </fieldset>
  );
}

function QuizSetupState({ message, spinning }: { message: string; spinning?: boolean }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="text-center text-sm text-muted-foreground">
        {spinning ? <LoaderCircle className="mx-auto mb-3 size-6 animate-spin" /> : null}
        {message}
      </div>
    </main>
  );
}

function subscribeToStorage(onStoreChange: () => void) {
  window.addEventListener("storage", onStoreChange);
  return () => window.removeEventListener("storage", onStoreChange);
}

function readStoredValue(): string | null {
  try {
    return window.sessionStorage.getItem(PENDING_QUIZ_SESSION_KEY);
  } catch {
    return null;
  }
}
