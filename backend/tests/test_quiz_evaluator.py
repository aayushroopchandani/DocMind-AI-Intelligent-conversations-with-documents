from __future__ import annotations

import unittest
from datetime import datetime, timezone

from db.models.attempt_quiz import QuizAttemptSubmission
from db.models.generated_quiz import (
    CorrectMatch,
    FillInTheBlankAnswer,
    FillInTheBlankQuestion,
    GeneratedQuizCreate,
    MatchItem,
    MatchTheFollowingQuestion,
    MCQOptions,
    MultipleCorrectMCQQuestion,
    MultipleCorrectScoring,
    OptionAnswer,
    QuizQuestionTopic,
    SingleCorrectMCQQuestion,
    TrueFalseQuestion,
)
from services.quiz_evaluator import QuizSubmissionError, evaluate_quiz


def _topic(*sub_topics: str) -> QuizQuestionTopic:
    return QuizQuestionTopic(
        main_topic="Neural Networks",
        sub_topics=list(sub_topics),
    )


def _options() -> MCQOptions:
    return MCQOptions(A="Alpha", B="Beta", C="Gamma", D="Delta")


def _full_quiz() -> GeneratedQuizCreate:
    return GeneratedQuizCreate(
        user_id="user-1",
        chat_id="chat-1",
        quiz_scope="topic_based",
        target="Neural Networks",
        number_of_questions=5,
        status="generated",
        questions=[
            SingleCorrectMCQQuestion(
                id="q1",
                topic=_topic("Core Concepts"),
                question="Choose Beta.",
                options=_options(),
                correct_answer=OptionAnswer(option="B", answer="Beta"),
            ),
            MultipleCorrectMCQQuestion(
                id="q2",
                topic=_topic("Core Concepts"),
                question="Choose Alpha and Gamma.",
                options=_options(),
                correct_answers=[
                    OptionAnswer(option="A", answer="Alpha"),
                    OptionAnswer(option="C", answer="Gamma"),
                ],
                scoring=MultipleCorrectScoring(allow_partial_credit=True),
            ),
            TrueFalseQuestion(
                id="q3",
                topic=_topic("Boolean Concepts"),
                statement="This statement is true.",
                correct_answer=True,
            ),
            FillInTheBlankQuestion(
                id="q4",
                topic=_topic("Optimization"),
                question="Complete both blanks.",
                blanks=[
                    FillInTheBlankAnswer(
                        blank_id="b1",
                        correct_answers=["gradient descent"],
                    ),
                    FillInTheBlankAnswer(
                        blank_id="b2",
                        correct_answers=["learning rate"],
                    ),
                ],
            ),
            MatchTheFollowingQuestion(
                id="q5",
                topic=_topic("Architectures"),
                question="Match the items.",
                left_items=[
                    MatchItem(id="l1", text="Left 1"),
                    MatchItem(id="l2", text="Left 2"),
                ],
                right_items=[
                    MatchItem(id="r1", text="Right 1"),
                    MatchItem(id="r2", text="Right 2"),
                ],
                correct_matches=[
                    CorrectMatch(left_id="l1", right_id="r1"),
                    CorrectMatch(left_id="l2", right_id="r2"),
                ],
            ),
        ],
    )


class QuizEvaluatorTests(unittest.TestCase):
    def test_all_question_formats_and_topic_aggregation(self) -> None:
        submitted_at = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        submission = QuizAttemptSubmission(
            answers=[
                {
                    "question_id": "q1",
                    "response": {"selected_options": ["B"]},
                    "time_taken_seconds": 10,
                },
                {
                    "question_id": "q2",
                    "response": {"selected_options": ["A"]},
                    "time_taken_seconds": 20,
                },
                {
                    "question_id": "q4",
                    "response": {
                        "blank_answers": [
                            {
                                "blank_id": "b1",
                                "answer": "  Gradient   Descent ",
                            },
                            {"blank_id": "b2", "answer": "momentum"},
                        ]
                    },
                    "time_taken_seconds": 30,
                },
                {
                    "question_id": "q5",
                    "response": {
                        "matches": [{"left_id": "l1", "right_id": "r1"}]
                    },
                    "time_taken_seconds": 40,
                },
            ]
        )

        attempt = evaluate_quiz(
            quiz_id="quiz-1",
            quiz=_full_quiz(),
            submission=submission,
            submitted_at=submitted_at,
        )

        self.assertEqual(attempt.status, "evaluated")
        self.assertEqual(attempt.duration_seconds, 100)
        self.assertEqual(
            attempt.started_at,
            datetime(2026, 7, 16, 11, 58, 20, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(attempt.result)
        self.assertEqual(attempt.result.score, 2.5)
        self.assertEqual(attempt.result.maximum_score, 5)
        self.assertEqual(attempt.result.percentage, 50)
        self.assertEqual(attempt.result.correct, 1)
        self.assertEqual(attempt.result.partially_correct, 3)
        self.assertEqual(attempt.result.skipped, 1)
        self.assertEqual(
            [answer.evaluation.status for answer in attempt.answers],
            [
                "correct",
                "partially_correct",
                "skipped",
                "partially_correct",
                "partially_correct",
            ],
        )

        main_topic = attempt.topic_performance[0]
        self.assertEqual(main_topic.main_topic, "Neural Networks")
        self.assertEqual(main_topic.percentage, 50)
        self.assertEqual(main_topic.attempted, 4)
        self.assertEqual(main_topic.status, "needs_practice")
        core = main_topic.sub_topics[0]
        self.assertEqual(core.name, "Core Concepts")
        self.assertEqual(core.percentage, 75)
        self.assertEqual(core.status, "strong")
        self.assertEqual(attempt.weak_topics, [])

    def test_repeated_incorrect_subtopic_is_marked_weak(self) -> None:
        quiz = GeneratedQuizCreate(
            user_id="user-1",
            chat_id="chat-1",
            quiz_scope="topic_based",
            number_of_questions=2,
            status="generated",
            questions=[
                SingleCorrectMCQQuestion(
                    id=f"q{index}",
                    topic=_topic("Back Propagation"),
                    question="Choose Alpha.",
                    options=_options(),
                    correct_answer=OptionAnswer(option="A", answer="Alpha"),
                )
                for index in (1, 2)
            ],
        )
        submission = QuizAttemptSubmission(
            answers=[
                {
                    "question_id": question_id,
                    "response": {"selected_options": ["D"]},
                }
                for question_id in ("q1", "q2")
            ]
        )

        attempt = evaluate_quiz(
            quiz_id="quiz-1",
            quiz=quiz,
            submission=submission,
        )

        self.assertEqual(attempt.topic_performance[0].status, "weak")
        self.assertEqual(len(attempt.weak_topics), 1)
        self.assertEqual(attempt.weak_topics[0].sub_topic, "Back Propagation")
        self.assertEqual(attempt.weak_topics[0].question_count, 2)

    def test_unknown_and_duplicate_question_ids_are_rejected(self) -> None:
        quiz = _full_quiz()
        with self.assertRaisesRegex(QuizSubmissionError, "Unknown question IDs"):
            evaluate_quiz(
                quiz_id="quiz-1",
                quiz=quiz,
                submission=QuizAttemptSubmission(
                    answers=[{"question_id": "q99"}]
                ),
            )

        with self.assertRaisesRegex(QuizSubmissionError, "more than once"):
            evaluate_quiz(
                quiz_id="quiz-1",
                quiz=quiz,
                submission=QuizAttemptSubmission(
                    answers=[
                        {"question_id": "q1"},
                        {"question_id": "q1"},
                    ]
                ),
            )

    def test_response_fields_must_match_question_type(self) -> None:
        with self.assertRaisesRegex(
            QuizSubmissionError,
            "unsupported response fields",
        ):
            evaluate_quiz(
                quiz_id="quiz-1",
                quiz=_full_quiz(),
                submission=QuizAttemptSubmission(
                    answers=[
                        {
                            "question_id": "q1",
                            "response": {
                                "selected_options": ["B"],
                                "boolean_answer": True,
                            },
                        }
                    ]
                ),
            )


if __name__ == "__main__":
    unittest.main()
