from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import HTTPException

from apis.quiz_attempts import submit_quiz_attempt
from db.models.attempt_quiz import QuizAttemptSubmission
from db.models.generated_quiz import (
    GeneratedQuizInDB,
    MCQOptions,
    OptionAnswer,
    QuizQuestionTopic,
    SingleCorrectMCQQuestion,
)


def _stored_quiz(quiz_id: str) -> dict:
    quiz = GeneratedQuizInDB(
        _id=quiz_id,
        user_id="user-1",
        chat_id="chat-1",
        quiz_scope="topic_based",
        number_of_questions=1,
        status="generated",
        questions=[
            SingleCorrectMCQQuestion(
                id="q1",
                topic=QuizQuestionTopic(
                    main_topic="Neural Networks",
                    sub_topics=["Back Propagation"],
                ),
                question="Choose Alpha.",
                options=MCQOptions(
                    A="Alpha",
                    B="Beta",
                    C="Gamma",
                    D="Delta",
                ),
                correct_answer=OptionAnswer(option="A", answer="Alpha"),
            )
        ],
    )
    return quiz.model_dump(mode="python", by_alias=False)


class QuizAttemptApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_submission_is_evaluated_and_persisted(self) -> None:
        quiz_id = str(ObjectId())
        attempt_id = str(ObjectId())
        now = datetime.now(timezone.utc)

        async def store_attempt(*, attempt):
            document = attempt.model_dump(mode="python")
            document.update(
                {
                    "id": attempt_id,
                    "attempt_number": 1,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            return document

        with (
            patch(
                "apis.quiz_attempts.crud.get_generated_quiz",
                new=AsyncMock(return_value=_stored_quiz(quiz_id)),
            ) as get_quiz,
            patch(
                "apis.quiz_attempts.crud.create_quiz_attempt",
                new=AsyncMock(side_effect=store_attempt),
            ) as create_attempt,
        ):
            result = await submit_quiz_attempt(
                quiz_id=quiz_id,
                body=QuizAttemptSubmission(
                    answers=[
                        {
                            "question_id": "q1",
                            "response": {"selected_options": ["A"]},
                        }
                    ]
                ),
                user_id="user-1",
                _=None,
            )

        get_quiz.assert_awaited_once_with(quiz_id=quiz_id, user_id="user-1")
        create_attempt.assert_awaited_once()
        self.assertEqual(result.id, attempt_id)
        self.assertEqual(result.user_id, "user-1")
        self.assertEqual(result.result.score, 1)

    async def test_missing_or_unowned_quiz_returns_not_found(self) -> None:
        with patch(
            "apis.quiz_attempts.crud.get_generated_quiz",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as context:
                await submit_quiz_attempt(
                    quiz_id=str(ObjectId()),
                    body=QuizAttemptSubmission(),
                    user_id="user-1",
                    _=None,
                )

        self.assertEqual(context.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
