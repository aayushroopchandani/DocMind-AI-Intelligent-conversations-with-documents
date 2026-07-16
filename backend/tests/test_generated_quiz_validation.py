from __future__ import annotations

import unittest

from db.models.generated_quiz import (
    MCQOptions,
    OptionAnswer,
    QuizQuestionTopic,
    SingleCorrectMCQQuestion,
)
from scripts.intention_pipelines.quiz_pipeline.topic_based import (
    TopicBasedQuizRequest,
    _validate_generated_questions,
)


def _question() -> SingleCorrectMCQQuestion:
    return SingleCorrectMCQQuestion(
        id="q1",
        topic=QuizQuestionTopic(
            main_topic="Neural Networks",
            sub_topics=["Back Propagation"],
        ),
        question="Choose Alpha.",
        options=MCQOptions(A="Alpha", B="Beta", C="Gamma", D="Delta"),
        correct_answer=OptionAnswer(option="A", answer="Alpha"),
    )


def _request(**overrides) -> TopicBasedQuizRequest:
    values = {
        "user_id": "user-1",
        "chat_id": "chat-1",
        "doc_ids": ["doc-1"],
        "target": "Neural Networks",
        "number_of_questions": 1,
        "question_formats": ["single_correct_mcq"],
    }
    values.update(overrides)
    return TopicBasedQuizRequest(**values)


class GeneratedQuizValidationTests(unittest.TestCase):
    def test_accepts_complete_requested_quiz(self) -> None:
        _validate_generated_questions([_question()], _request())

    def test_rejects_incomplete_question_count(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "incomplete quiz"):
            _validate_generated_questions(
                [_question()],
                _request(number_of_questions=2),
            )

    def test_rejects_unrequested_question_format(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unrequested question format"):
            _validate_generated_questions(
                [_question()],
                _request(question_formats=["true_false"]),
            )


if __name__ == "__main__":
    unittest.main()
