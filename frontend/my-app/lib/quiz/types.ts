/**
 * Frontend quiz domain types.
 *
 * These mirror the JSON emitted by the backend `GeneratedQuiz` model 1:1
 * (snake_case, discriminated on `type`) so the practice UI can be wired to the
 * real `type: "quiz"` SSE payload later without any shape translation.
 */

export type OptionKey = "A" | "B" | "C" | "D";

export type QuizScope =
  | "context_based"
  | "topic_based"
  | "structure_based"
  | "whole_document";

export type QuizMode = "practice" | "rapid_fire" | "exam_mode";
export type QuizDifficulty = "easy" | "medium" | "hard";
export type QuizStatus = "draft" | "generating" | "generated" | "failed";

export type QuizQuestionFormat =
  | "single_correct_mcq"
  | "multiple_correct_mcq"
  | "true_false"
  | "fill_in_the_blank"
  | "match_the_following";

export interface QuizCitation {
  document_id: string;
  document_name: string;
  page_number?: number | null;
  chunk_id?: string | null;
  excerpt?: string | null;
}

interface QuizQuestionBase {
  id: string;
  short_explanation?: string;
  citations?: QuizCitation[];
}

export interface MCQOptions {
  A: string;
  B: string;
  C: string;
  D: string;
}

export interface OptionAnswer {
  option: OptionKey;
  answer: string;
}

export interface SingleCorrectMCQQuestion extends QuizQuestionBase {
  type: "single_correct_mcq";
  question: string;
  options: MCQOptions;
  correct_answer: OptionAnswer;
}

export interface MultipleCorrectMCQQuestion extends QuizQuestionBase {
  type: "multiple_correct_mcq";
  question: string;
  options: MCQOptions;
  correct_answers: OptionAnswer[];
  scoring?: {
    requires_all_correct: boolean;
    allow_partial_credit: boolean;
  };
}

export interface TrueFalseQuestion extends QuizQuestionBase {
  type: "true_false";
  statement: string;
  correct_answer: boolean;
}

export interface FillInTheBlankAnswer {
  blank_id: string;
  correct_answers: string[];
  case_sensitive: boolean;
}

export interface FillInTheBlankQuestion extends QuizQuestionBase {
  type: "fill_in_the_blank";
  question: string;
  blanks: FillInTheBlankAnswer[];
}

export interface MatchItem {
  id: string;
  text: string;
}

export interface CorrectMatch {
  left_id: string;
  right_id: string;
}

export interface MatchTheFollowingQuestion extends QuizQuestionBase {
  type: "match_the_following";
  question: string;
  left_items: MatchItem[];
  right_items: MatchItem[];
  correct_matches: CorrectMatch[];
}

export type QuizQuestion =
  | SingleCorrectMCQQuestion
  | MultipleCorrectMCQQuestion
  | TrueFalseQuestion
  | FillInTheBlankQuestion
  | MatchTheFollowingQuestion;

export interface Quiz {
  user_id: string;
  chat_id: string;
  doc_ids: string[];
  quiz_scope: QuizScope;
  target?: string | null;
  mode?: QuizMode | null;
  number_of_questions: number;
  difficulty: QuizDifficulty;
  question_formats: QuizQuestionFormat[];
  status: QuizStatus;
  questions: QuizQuestion[];
}

/* ------------------------------------------------------------------ */
/* Client-side answer + grading shapes                                 */
/* ------------------------------------------------------------------ */

/**
 * The in-progress answer a learner has selected for a question, keyed by
 * question `type`. `null`/empty means "not answered yet".
 */
export type QuizAnswer =
  | { type: "single_correct_mcq"; option: OptionKey | null }
  | { type: "multiple_correct_mcq"; options: OptionKey[] }
  | { type: "true_false"; value: boolean | null }
  | { type: "fill_in_the_blank"; values: Record<string, string> }
  | { type: "match_the_following"; pairs: Record<string, string> };

/** Result of grading a single answered question. */
export interface GradedResult {
  /** Whole-question correctness (used for scoring + top-level feedback). */
  correct: boolean;
  /**
   * Optional per-part correctness, used to paint individual blanks / matches
   * / options green or red in the review UI.
   */
  parts?: Record<string, boolean>;
}
