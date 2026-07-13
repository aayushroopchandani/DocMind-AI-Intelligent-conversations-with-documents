/**
 * Static demo quiz used to develop the practice UI in isolation.
 *
 * Replace this with the `type: "quiz"` SSE payload when wiring the backend;
 * the shape is identical to the `Quiz` type it satisfies.
 */

import type { Quiz } from "./types";

const DOC = "Introduction to Machine Learning with Python.pdf";
const DOC_ID = "doc_hash_abc";

export const SAMPLE_PRACTICE_QUIZ: Quiz = {
  user_id: "user_123",
  chat_id: "chat_456",
  doc_ids: [DOC_ID],
  quiz_scope: "topic_based",
  target: "Supervised Learning & k-NN",
  mode: "practice",
  number_of_questions: 5,
  difficulty: "medium",
  question_formats: [
    "single_correct_mcq",
    "multiple_correct_mcq",
    "true_false",
    "fill_in_the_blank",
    "match_the_following",
  ],
  status: "generated",
  questions: [
    {
      id: "q1",
      type: "single_correct_mcq",
      question:
        "In k-Nearest Neighbors, what does increasing the value of k generally do to the decision boundary?",
      options: {
        A: "Makes it smoother and less sensitive to noise",
        B: "Makes it more jagged and sensitive to individual points",
        C: "Has no effect on the boundary",
        D: "Guarantees zero training error",
      },
      correct_answer: {
        option: "A",
        answer: "Makes it smoother and less sensitive to noise",
      },
      short_explanation:
        "A larger k averages over more neighbors, so single noisy points have less influence and the boundary becomes smoother (higher bias, lower variance).",
      citations: [
        {
          document_id: DOC_ID,
          document_name: DOC,
          page_number: 37,
          chunk_id: "chunk_knn_01",
          excerpt:
            "Considering more neighbors leads to a smoother decision boundary, corresponding to a simpler model.",
        },
      ],
    },
    {
      id: "q2",
      type: "multiple_correct_mcq",
      question:
        "Which of the following are true of supervised learning? (Select all that apply)",
      options: {
        A: "It requires labeled training examples",
        B: "Classification and regression are both supervised tasks",
        C: "It discovers structure without any labels",
        D: "The goal is to generalize to unseen inputs",
      },
      correct_answers: [
        { option: "A", answer: "It requires labeled training examples" },
        {
          option: "B",
          answer: "Classification and regression are both supervised tasks",
        },
        { option: "D", answer: "The goal is to generalize to unseen inputs" },
      ],
      scoring: { requires_all_correct: true, allow_partial_credit: false },
      short_explanation:
        "Supervised learning learns a mapping from labeled inputs to outputs and aims to generalize. Learning without labels (C) is unsupervised learning.",
      citations: [
        {
          document_id: DOC_ID,
          document_name: DOC,
          page_number: 25,
          chunk_id: "chunk_sup_01",
          excerpt:
            "In supervised learning the user provides the algorithm with pairs of inputs and desired outputs.",
        },
      ],
    },
    {
      id: "q3",
      type: "true_false",
      statement:
        "A model that achieves very high accuracy on the training set but low accuracy on the test set is said to be underfitting.",
      correct_answer: false,
      short_explanation:
        "That pattern describes overfitting — the model memorizes training data and fails to generalize. Underfitting is poor performance on both sets.",
      citations: [
        {
          document_id: DOC_ID,
          document_name: DOC,
          page_number: 27,
          chunk_id: "chunk_fit_01",
          excerpt:
            "Building a model that is too complex for the amount of information we have is called overfitting.",
        },
      ],
    },
    {
      id: "q4",
      type: "fill_in_the_blank",
      question:
        "The k-NN algorithm is often called a ___ learner because it does no real work at training time and defers computation to ___ time.",
      blanks: [
        {
          blank_id: "blank_1",
          correct_answers: ["lazy"],
          case_sensitive: false,
        },
        {
          blank_id: "blank_2",
          correct_answers: ["prediction", "query", "test"],
          case_sensitive: false,
        },
      ],
      short_explanation:
        "k-NN is a lazy learner: it simply stores the training set and only computes distances when a prediction is requested.",
      citations: [
        {
          document_id: DOC_ID,
          document_name: DOC,
          page_number: 35,
          chunk_id: "chunk_knn_02",
          excerpt:
            "Building the nearest neighbors model only consists of storing the training dataset.",
        },
      ],
    },
    {
      id: "q5",
      type: "match_the_following",
      question: "Match each algorithm to the category it belongs to.",
      left_items: [
        { id: "l1", text: "k-Nearest Neighbors" },
        { id: "l2", text: "Linear Regression" },
        { id: "l3", text: "k-Means" },
        { id: "l4", text: "PCA" },
      ],
      right_items: [
        { id: "r1", text: "Instance-based classification" },
        { id: "r2", text: "Supervised regression" },
        { id: "r3", text: "Unsupervised clustering" },
        { id: "r4", text: "Dimensionality reduction" },
      ],
      correct_matches: [
        { left_id: "l1", right_id: "r1" },
        { left_id: "l2", right_id: "r2" },
        { left_id: "l3", right_id: "r3" },
        { left_id: "l4", right_id: "r4" },
      ],
      short_explanation:
        "k-NN is instance-based, linear regression predicts continuous targets, k-Means groups unlabeled points, and PCA compresses features into fewer dimensions.",
      citations: [
        {
          document_id: DOC_ID,
          document_name: DOC,
          page_number: 131,
          chunk_id: "chunk_cat_01",
          excerpt:
            "Unsupervised learning subsumes all kinds of machine learning where there is no known output.",
        },
      ],
    },
  ],
};

/** Same questions, presented under the rapid-fire and exam experiences. */
export const SAMPLE_RAPID_FIRE_QUIZ: Quiz = {
  ...SAMPLE_PRACTICE_QUIZ,
  mode: "rapid_fire",
};

export const SAMPLE_EXAM_QUIZ: Quiz = {
  ...SAMPLE_PRACTICE_QUIZ,
  mode: "exam_mode",
};
