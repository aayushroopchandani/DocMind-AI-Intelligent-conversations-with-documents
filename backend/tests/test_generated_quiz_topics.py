from __future__ import annotations

import unittest

from db.models.generated_quiz import (
    GeneratedQuizCreate,
    MCQOptions,
    OptionAnswer,
    QuizQuestionTopic,
    SingleCorrectMCQQuestion,
)
from scripts.intention_pipelines.quiz_pipeline.topic_based import (
    _post_process_questions,
)


def _question(
    question_id: str,
    *,
    main_topic: str,
    sub_topics: list[str],
) -> SingleCorrectMCQQuestion:
    return SingleCorrectMCQQuestion(
        id=question_id,
        question="What is the correct answer?",
        options=MCQOptions(A="A1", B="B1", C="C1", D="D1"),
        correct_answer=OptionAnswer(option="A", answer="A1"),
        topic=QuizQuestionTopic(
            main_topic=main_topic,
            sub_topics=sub_topics,
        ),
    )


class GeneratedQuizTopicTests(unittest.TestCase):
    def test_post_processing_reuses_canonical_topic_labels(self) -> None:
        questions = [
            _question(
                "first",
                main_topic="Neural Networks",
                sub_topics=["Back Propagation", " Back  Propagation "],
            ),
            _question(
                "second",
                main_topic=" neural networks ",
                sub_topics=["back propagation", "Gradient Descent"],
            ),
        ]

        processed = _post_process_questions(questions, [], 2)

        self.assertEqual(
            [question.topic.main_topic for question in processed],
            ["Neural Networks", "Neural Networks"],
        )
        self.assertEqual(processed[0].topic.sub_topics, ["Back Propagation"])
        self.assertEqual(
            processed[1].topic.sub_topics,
            ["Back Propagation", "Gradient Descent"],
        )

    def test_topic_is_in_generated_quiz_document(self) -> None:
        quiz = GeneratedQuizCreate(
            user_id="user-1",
            chat_id="chat-1",
            doc_ids=["doc-1"],
            quiz_scope="topic_based",
            target="Neural Networks",
            number_of_questions=1,
            question_formats=["single_correct_mcq"],
            status="generated",
            questions=[
                _question(
                    "q1",
                    main_topic="Neural Networks",
                    sub_topics=["Back Propagation"],
                )
            ],
        )

        document = quiz.model_dump(mode="python")

        self.assertEqual(
            document["questions"][0]["topic"],
            {
                "main_topic": "Neural Networks",
                "sub_topics": ["Back Propagation"],
            },
        )


if __name__ == "__main__":
    unittest.main()
