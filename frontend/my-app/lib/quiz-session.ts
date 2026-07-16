import type {
  BackendIntentData,
  MissingQuizConfigurationField,
  QuizMode,
} from "@/lib/types";

export const PENDING_QUIZ_SESSION_KEY = "docmind:pending-quiz-generation";
const PENDING_QUIZ_TTL_MS = 2 * 60 * 60 * 1000;

export interface PendingQuizGeneration {
  chatId: string;
  question: string;
  documentIds: string[];
  messageId: string;
  sourceMessageId: string;
  missingFields: MissingQuizConfigurationField[];
  intent: BackendIntentData;
  createdAt: number;
}

const MODE_PATH: Record<QuizMode, string> = {
  practice: "/quiz/practice",
  rapid_fire: "/quiz/rapid-fire",
  exam_mode: "/quiz/exam",
};

/** Build the durable route used by live and persisted quiz chat cards. */
export function quizHref(quizId: string, mode: QuizMode): string {
  return `${MODE_PATH[mode]}?quizId=${encodeURIComponent(quizId)}`;
}

function isPendingQuizGeneration(value: unknown): value is PendingQuizGeneration {
  if (!value || typeof value !== "object") return false;

  const pending = value as Partial<PendingQuizGeneration>;
  return (
    typeof pending.chatId === "string" &&
    typeof pending.question === "string" &&
    Array.isArray(pending.documentIds) &&
    pending.documentIds.every((id) => typeof id === "string") &&
    typeof pending.messageId === "string" &&
    typeof pending.sourceMessageId === "string" &&
    Array.isArray(pending.missingFields) &&
    pending.missingFields.every(
      (field) =>
        field === "mode" ||
        field === "number_of_questions" ||
        field === "question_formats",
    ) &&
    !!pending.intent &&
    pending.intent.intent === "quiz" &&
    typeof pending.createdAt === "number"
  );
}

/** Store the deferred quiz request for the setup route in this browser tab. */
export function savePendingQuizGeneration(
  pending: PendingQuizGeneration,
): boolean {
  if (typeof window === "undefined") return false;

  try {
    window.sessionStorage.setItem(
      PENDING_QUIZ_SESSION_KEY,
      JSON.stringify(pending),
    );
    return true;
  } catch {
    return false;
  }
}

/** Read a pending request, ignoring invalid or corrupt session data. */
export function readPendingQuizGeneration(): PendingQuizGeneration | null {
  if (typeof window === "undefined") return null;

  try {
    const raw = window.sessionStorage.getItem(PENDING_QUIZ_SESSION_KEY);
    if (!raw) return null;

    const value: unknown = JSON.parse(raw);
    if (
      isPendingQuizGeneration(value) &&
      value.createdAt <= Date.now() &&
      Date.now() - value.createdAt <= PENDING_QUIZ_TTL_MS
    ) {
      return value;
    }
    window.sessionStorage.removeItem(PENDING_QUIZ_SESSION_KEY);
  } catch {
    // Storage may be unavailable or contain malformed JSON.
  }
  return null;
}

export function clearPendingQuizGeneration(): void {
  if (typeof window === "undefined") return;

  try {
    window.sessionStorage.removeItem(PENDING_QUIZ_SESSION_KEY);
  } catch {
    // Storage may be unavailable in privacy-restricted browser contexts.
  }
}
