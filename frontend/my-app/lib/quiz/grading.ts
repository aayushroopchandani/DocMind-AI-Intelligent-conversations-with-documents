import type { QuizAnswer, QuizQuestion } from "./types";

/** Whether a learner has entered any meaningful response for a question. */
export function isAnswerComplete(answer: QuizAnswer): boolean {
  switch (answer.type) {
    case "single_correct_mcq":
      return answer.option !== null;
    case "multiple_correct_mcq":
      return answer.options.length > 0;
    case "true_false":
      return answer.value !== null;
    case "fill_in_the_blank":
      return Object.values(answer.values).some((value) => value.trim().length > 0);
    case "match_the_following":
      return Object.keys(answer.pairs).length > 0;
  }
}

/** Create a fresh client response; correctness is always decided by the backend. */
export function emptyAnswer(question: QuizQuestion): QuizAnswer {
  switch (question.type) {
    case "single_correct_mcq":
      return { type: "single_correct_mcq", option: null };
    case "multiple_correct_mcq":
      return { type: "multiple_correct_mcq", options: [] };
    case "true_false":
      return { type: "true_false", value: null };
    case "fill_in_the_blank":
      return { type: "fill_in_the_blank", values: {} };
    case "match_the_following":
      return { type: "match_the_following", pairs: {} };
  }
}
