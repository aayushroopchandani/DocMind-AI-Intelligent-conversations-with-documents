/**
 * Frontend quiz domain types.
 *
 * Playable question types intentionally omit solutions. Full solved questions
 * appear only in an evaluated attempt's `review_questions` payload.
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
  topic?: {
    main_topic: string;
    sub_topics: string[];
  };
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
  correct_answer?: OptionAnswer;
}

export interface MultipleCorrectMCQQuestion extends QuizQuestionBase {
  type: "multiple_correct_mcq";
  question: string;
  options: MCQOptions;
  correct_answers?: OptionAnswer[];
  scoring?: {
    requires_all_correct: boolean;
    allow_partial_credit: boolean;
  };
}

export interface TrueFalseQuestion extends QuizQuestionBase {
  type: "true_false";
  statement: string;
  correct_answer?: boolean;
}

export interface FillInTheBlankAnswer {
  blank_id: string;
  correct_answers?: string[];
  case_sensitive?: boolean;
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
  correct_matches?: CorrectMatch[];
}

export type QuizQuestion =
  | SingleCorrectMCQQuestion
  | MultipleCorrectMCQQuestion
  | TrueFalseQuestion
  | FillInTheBlankQuestion
  | MatchTheFollowingQuestion;

/** Full solved question returned only after the backend evaluates an attempt. */
export type ReviewQuizQuestion =
  | (SingleCorrectMCQQuestion & { correct_answer: OptionAnswer })
  | (MultipleCorrectMCQQuestion & { correct_answers: OptionAnswer[] })
  | (TrueFalseQuestion & { correct_answer: boolean })
  | (Omit<FillInTheBlankQuestion, "blanks"> & {
      type: "fill_in_the_blank";
      blanks: (FillInTheBlankAnswer & {
        correct_answers: string[];
        case_sensitive: boolean;
      })[];
    })
  | (MatchTheFollowingQuestion & { correct_matches: CorrectMatch[] });

export interface Quiz {
  /** MongoDB id returned by the persisted generated-quiz endpoint. */
  id?: string;
  user_id?: string;
  chat_id?: string;
  doc_ids?: string[];
  source_message_id?: string | null;
  response_message_id?: string | null;
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
/* Client-side in-progress answer shapes                               */
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

/* ------------------------------------------------------------------ */
/* Backend-authoritative attempt shapes                                */
/* ------------------------------------------------------------------ */

export type QuizAnswerStatus =
  | "correct"
  | "incorrect"
  | "partially_correct"
  | "skipped";

export interface QuizQuestionResponse {
  selected_options: OptionKey[];
  boolean_answer: boolean | null;
  blank_answers: { blank_id: string; answer: string }[];
  matches: { left_id: string; right_id: string }[];
}

export interface QuizAnswerSubmission {
  question_id: string;
  response: QuizQuestionResponse;
  time_taken_seconds: number;
}

export interface QuizAnswerEvaluation {
  status: QuizAnswerStatus;
  is_correct: boolean;
  awarded_marks: number;
  maximum_marks: number;
}

export interface QuizAttemptAnswer {
  question_id: string;
  question_type: QuizQuestionFormat;
  response: QuizQuestionResponse;
  evaluation: QuizAnswerEvaluation | null;
  topic: {
    main_topic: string;
    sub_topics: string[];
  };
  time_taken_seconds: number;
  answered_at?: string | null;
}

export interface QuizAttemptResult {
  score: number;
  maximum_score: number;
  percentage: number;
  correct: number;
  incorrect: number;
  partially_correct: number;
  skipped: number;
}

export interface SubTopicPerformance {
  name: string;
  correct: number;
  attempted: number;
  total_questions: number;
  percentage: number;
  status: "weak" | "needs_practice" | "strong" | "insufficient_data";
}

export interface MainTopicPerformance {
  main_topic: string;
  correct: number;
  partially_correct: number;
  incorrect: number;
  attempted: number;
  total_questions: number;
  percentage: number;
  status: "weak" | "needs_practice" | "strong" | "insufficient_data";
  sub_topics: SubTopicPerformance[];
}

export interface QuizAttempt {
  id?: string;
  quiz_id: string;
  user_id: string;
  chat_id: string;
  submission_id?: string | null;
  attempt_number: number;
  status: "in_progress" | "submitted" | "evaluated" | "abandoned";
  started_at: string;
  submitted_at?: string | null;
  duration_seconds?: number | null;
  answers: QuizAttemptAnswer[];
  result: QuizAttemptResult | null;
  topic_performance: MainTopicPerformance[];
  weak_topics: {
    main_topic: string;
    sub_topic: string;
    percentage: number;
    question_count: number;
  }[];
  /** Solved definitions returned transiently after evaluation, never on quiz GET. */
  review_questions: ReviewQuizQuestion[];
  created_at: string;
  updated_at: string;
}
