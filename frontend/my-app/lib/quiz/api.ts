import type {
  Quiz,
  QuizAnswer,
  QuizAnswerSubmission,
  QuizAttempt,
  QuizQuestionResponse,
} from "./types";
import { emptyAnswer } from "./grading";

export function createQuizSubmissionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function responseError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as {
      detail?: string;
      error?: string;
    };
    return payload.detail ?? payload.error ?? response.statusText;
  } catch {
    return response.statusText || "Request failed";
  }
}

export async function getGeneratedQuiz(
  quizId: string,
  signal?: AbortSignal,
): Promise<Quiz> {
  const response = await fetch(`/api/quizzes/${encodeURIComponent(quizId)}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw new Error(await responseError(response));

  const quiz = (await response.json()) as Quiz & { _id?: string };
  return { ...quiz, id: quiz.id ?? quiz._id ?? quizId };
}

function emptyResponse(): QuizQuestionResponse {
  return {
    selected_options: [],
    boolean_answer: null,
    blank_answers: [],
    matches: [],
  };
}

export function toAnswerSubmission(
  questionId: string,
  answer: QuizAnswer,
  timeTakenSeconds = 0,
): QuizAnswerSubmission {
  const response = emptyResponse();

  switch (answer.type) {
    case "single_correct_mcq":
      response.selected_options = answer.option ? [answer.option] : [];
      break;
    case "multiple_correct_mcq":
      response.selected_options = answer.options;
      break;
    case "true_false":
      response.boolean_answer = answer.value;
      break;
    case "fill_in_the_blank":
      response.blank_answers = Object.entries(answer.values)
        .filter(([, value]) => value.trim().length > 0)
        .map(([blank_id, answer]) => ({ blank_id, answer }));
      break;
    case "match_the_following":
      response.matches = Object.entries(answer.pairs)
        .filter(([, rightId]) => Boolean(rightId))
        .map(([left_id, right_id]) => ({ left_id, right_id }));
      break;
  }

  return {
    question_id: questionId,
    response,
    time_taken_seconds: Math.max(0, Math.round(timeTakenSeconds)),
  };
}

export async function submitQuizAttempt(
  quiz: Quiz,
  answers: QuizAnswer[],
  submissionId: string,
  timeTakenSeconds: number[] = [],
  signal?: AbortSignal,
): Promise<QuizAttempt> {
  if (!quiz.id) throw new Error("This quiz has not been persisted yet.");

  const response = await fetch(
    `/api/quizzes/${encodeURIComponent(quiz.id)}/attempts`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        submission_id: submissionId,
        answers: quiz.questions.map((question, index) =>
          toAnswerSubmission(
            question.id,
            answers[index] ?? emptyAnswer(question),
            timeTakenSeconds[index],
          ),
        ),
      }),
      signal,
    },
  );

  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<QuizAttempt>;
}
