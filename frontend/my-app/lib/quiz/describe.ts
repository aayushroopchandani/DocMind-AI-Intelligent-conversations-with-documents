/**
 * Human-readable renderings of the correct answer and of the learner's own
 * answer, used by the review screens (rapid-fire + exam) where we show what was
 * picked versus what was right.
 */

import type { QuizAnswer, QuizQuestion, ReviewQuizQuestion } from "./types";

const NOT_ANSWERED = "Not answered";

export function describeCorrect(question: ReviewQuizQuestion): string {
  switch (question.type) {
    case "single_correct_mcq": {
      const key = question.correct_answer.option;
      return `${key}. ${question.options[key]}`;
    }
    case "multiple_correct_mcq":
      return question.correct_answers
        .map((a) => `${a.option}. ${question.options[a.option]}`)
        .join("  •  ");
    case "true_false":
      return question.correct_answer ? "True" : "False";
    case "fill_in_the_blank":
      return question.blanks
        .map((b, i) => `(${i + 1}) ${b.correct_answers.join(" / ")}`)
        .join("   ");
    case "match_the_following": {
      const left = new Map(question.left_items.map((l) => [l.id, l.text]));
      const right = new Map(question.right_items.map((r) => [r.id, r.text]));
      return question.correct_matches
        .map((m) => `${left.get(m.left_id)} → ${right.get(m.right_id)}`)
        .join("  •  ");
    }
  }
}

export function describeAnswer(
  question: QuizQuestion,
  answer: QuizAnswer,
): string {
  switch (question.type) {
    case "single_correct_mcq": {
      if (answer.type !== "single_correct_mcq" || !answer.option)
        return NOT_ANSWERED;
      return `${answer.option}. ${question.options[answer.option]}`;
    }
    case "multiple_correct_mcq": {
      if (answer.type !== "multiple_correct_mcq" || answer.options.length === 0)
        return NOT_ANSWERED;
      return answer.options
        .map((o) => `${o}. ${question.options[o]}`)
        .join("  •  ");
    }
    case "true_false": {
      if (answer.type !== "true_false" || answer.value === null)
        return NOT_ANSWERED;
      return answer.value ? "True" : "False";
    }
    case "fill_in_the_blank": {
      if (answer.type !== "fill_in_the_blank") return NOT_ANSWERED;
      const filled = question.blanks.map(
        (b, i) => `(${i + 1}) ${answer.values[b.blank_id]?.trim() || "—"}`,
      );
      const anyFilled = question.blanks.some((b) =>
        answer.values[b.blank_id]?.trim(),
      );
      return anyFilled ? filled.join("   ") : NOT_ANSWERED;
    }
    case "match_the_following": {
      if (answer.type !== "match_the_following") return NOT_ANSWERED;
      const left = new Map(question.left_items.map((l) => [l.id, l.text]));
      const right = new Map(question.right_items.map((r) => [r.id, r.text]));
      const pairs = question.left_items
        .filter((l) => answer.pairs[l.id])
        .map((l) => `${left.get(l.id)} → ${right.get(answer.pairs[l.id])}`);
      return pairs.length ? pairs.join("  •  ") : NOT_ANSWERED;
    }
  }
}
