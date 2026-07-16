import { streamChat } from "@/lib/api";
import type { QuizMode, QuizQuestionFormat } from "./types";

export interface ConfiguredQuizRequest {
  chatId: string;
  question: string;
  documentIds?: string[] | null;
  messageId: string;
  sourceMessageId: string;
  mode: QuizMode;
  numberOfQuestions: number;
  questionFormats: QuizQuestionFormat[];
}

interface GeneratedQuizReference {
  id: string;
  mode?: QuizMode | null;
}

/** Generate a quiz after the missing intent fields have been chosen in the UI. */
export async function generateConfiguredQuiz(
  request: ConfiguredQuizRequest,
  onStatus?: (message: string) => void,
  signal?: AbortSignal,
): Promise<GeneratedQuizReference> {
  let generatedQuiz: GeneratedQuizReference | undefined;
  let streamError: string | undefined;

  await streamChat(
    request.chatId,
    request.question,
    request.documentIds ?? undefined,
    {
      onStatus: (message) => onStatus?.(message),
      onToken: () => {},
      onCitations: () => {},
      onFinal: () => {},
      onQuiz: (quiz) => {
        generatedQuiz = { id: quiz.id, mode: quiz.mode };
      },
      onError: (message) => {
        streamError = message;
      },
    },
    {
      messageId: request.messageId,
      quizConfig: {
        sourceMessageId: request.sourceMessageId,
        mode: request.mode,
        numberOfQuestions: request.numberOfQuestions,
        questionFormats: request.questionFormats,
      },
      signal,
    },
  );

  if (streamError) throw new Error(streamError);
  if (!generatedQuiz?.id) {
    throw new Error("The quiz was generated without a persisted quiz id.");
  }
  return generatedQuiz;
}
