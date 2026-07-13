/**
 * Pure, framework-free grading for each quiz question format.
 *
 * Keeping this isolated from React means the same logic can be reused by
 * rapid-fire and exam modes (and unit-tested) without pulling in the UI.
 */

import type {
  GradedResult,
  QuizAnswer,
  QuizQuestion,
  FillInTheBlankQuestion,
  MatchTheFollowingQuestion,
  MultipleCorrectMCQQuestion,
} from "./types";

function normalize(value: string, caseSensitive: boolean): string {
  const trimmed = value.trim();
  return caseSensitive ? trimmed : trimmed.toLowerCase();
}

function gradeFillInTheBlank(
  question: FillInTheBlankQuestion,
  values: Record<string, string>,
): GradedResult {
  const parts: Record<string, boolean> = {};
  for (const blank of question.blanks) {
    const given = values[blank.blank_id] ?? "";
    const accepted = blank.correct_answers.map((a) =>
      normalize(a, blank.case_sensitive),
    );
    parts[blank.blank_id] =
      given.trim().length > 0 &&
      accepted.includes(normalize(given, blank.case_sensitive));
  }
  return { correct: Object.values(parts).every(Boolean), parts };
}

function gradeMatchTheFollowing(
  question: MatchTheFollowingQuestion,
  pairs: Record<string, string>,
): GradedResult {
  const parts: Record<string, boolean> = {};
  for (const match of question.correct_matches) {
    parts[match.left_id] = pairs[match.left_id] === match.right_id;
  }
  return { correct: Object.values(parts).every(Boolean), parts };
}

function gradeMultipleCorrect(
  question: MultipleCorrectMCQQuestion,
  selected: string[],
): GradedResult {
  const correctSet = new Set(question.correct_answers.map((a) => a.option));
  const selectedSet = new Set(selected);
  const parts: Record<string, boolean> = {};
  for (const key of ["A", "B", "C", "D"]) {
    // An option is "right" if its selection state matches its truth.
    parts[key] = correctSet.has(key as never) === selectedSet.has(key as never);
  }
  const exactMatch =
    correctSet.size === selectedSet.size &&
    [...correctSet].every((o) => selectedSet.has(o));
  return { correct: exactMatch, parts };
}

/**
 * Grade an answer against its question. Answer/question `type`s are assumed to
 * be aligned by the caller (they always are — both come from the same item).
 */
export function gradeQuestion(
  question: QuizQuestion,
  answer: QuizAnswer,
): GradedResult {
  switch (question.type) {
    case "single_correct_mcq":
      return {
        correct:
          answer.type === "single_correct_mcq" &&
          answer.option === question.correct_answer.option,
      };
    case "multiple_correct_mcq":
      return answer.type === "multiple_correct_mcq"
        ? gradeMultipleCorrect(question, answer.options)
        : { correct: false };
    case "true_false":
      return {
        correct:
          answer.type === "true_false" &&
          answer.value === question.correct_answer,
      };
    case "fill_in_the_blank":
      return answer.type === "fill_in_the_blank"
        ? gradeFillInTheBlank(question, answer.values)
        : { correct: false };
    case "match_the_following":
      return answer.type === "match_the_following"
        ? gradeMatchTheFollowing(question, answer.pairs)
        : { correct: false };
  }
}

/** Whether the learner has entered enough to submit (enables the check button). */
export function isAnswerComplete(answer: QuizAnswer): boolean {
  switch (answer.type) {
    case "single_correct_mcq":
      return answer.option !== null;
    case "multiple_correct_mcq":
      return answer.options.length > 0;
    case "true_false":
      return answer.value !== null;
    case "fill_in_the_blank":
      return Object.values(answer.values).some((v) => v.trim().length > 0);
    case "match_the_following":
      return Object.keys(answer.pairs).length > 0;
  }
}

/** A fresh, empty answer for a question — the initial state before interaction. */
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
