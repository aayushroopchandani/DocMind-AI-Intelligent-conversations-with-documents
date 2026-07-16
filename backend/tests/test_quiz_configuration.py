from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from apis import chats as chats_api
from scripts.intent_detection import (
    DetectedIntent,
    IntentType,
    MentionStatus,
    QuizMode,
    QuizQuestionFormat,
    QuizScope,
)
from utils.pydantic_schemas import QuizGenerationConfig


def _quiz_intent(**overrides) -> DetectedIntent:
    values = {
        "intent": IntentType.QUIZ,
        "quiz_scope": QuizScope.CONTEXT_BASED,
        "mode": QuizMode.PRACTICE,
        "mode_mention_status": MentionStatus.NOT_MENTIONED,
        "number_of_questions": 5,
        "number_of_questions_mention_status": MentionStatus.NOT_MENTIONED,
        "question_formats": [QuizQuestionFormat.SINGLE_CORRECT_MCQ],
        "question_formats_mention_status": MentionStatus.NOT_MENTIONED,
        "confidence": 1.0,
    }
    values.update(overrides)
    return DetectedIntent(**values)


class QuizConfigurationTests(unittest.TestCase):
    def test_detector_defaults_are_reported_as_missing_when_user_omitted_them(
        self,
    ) -> None:
        resolved = chats_api._resolve_quiz_config(_quiz_intent(), None)

        self.assertIsNone(resolved.mode)
        self.assertIsNone(resolved.number_of_questions)
        self.assertEqual(resolved.question_formats, [])
        self.assertEqual(
            resolved.missing_fields,
            ["mode", "number_of_questions", "question_formats"],
        )

    def test_frontend_config_fills_values_omitted_from_prompt(self) -> None:
        resolved = chats_api._resolve_quiz_config(
            _quiz_intent(),
            QuizGenerationConfig(
                mode="exam_mode",
                number_of_questions=10,
                question_formats=["true_false", "multiple_correct_mcq"],
            ),
        )

        self.assertEqual(resolved.mode, "exam_mode")
        self.assertEqual(resolved.number_of_questions, 10)
        self.assertEqual(
            resolved.question_formats,
            ["true_false", "multiple_correct_mcq"],
        )
        self.assertEqual(resolved.missing_fields, [])

    def test_explicit_prompt_values_win_over_frontend_config(self) -> None:
        intent = _quiz_intent(
            mode=QuizMode.RAPID_FIRE,
            mode_mention_status=MentionStatus.MENTIONED,
            number_of_questions=15,
            number_of_questions_mention_status=MentionStatus.MENTIONED,
            question_formats=[QuizQuestionFormat.FILL_IN_THE_BLANK],
            question_formats_mention_status=MentionStatus.MENTIONED,
        )

        resolved = chats_api._resolve_quiz_config(
            intent,
            QuizGenerationConfig(
                mode="practice",
                number_of_questions=5,
                question_formats=["single_correct_mcq"],
            ),
        )

        self.assertEqual(resolved.mode, "rapid_fire")
        self.assertEqual(resolved.number_of_questions, 15)
        self.assertEqual(resolved.question_formats, ["fill_in_the_blank"])
        self.assertEqual(resolved.missing_fields, [])

    def test_deferred_request_is_not_reused_as_its_own_quiz_context(self) -> None:
        conversation = [
            {
                "id": "earlier-answer",
                "role": "assistant",
                "content": "Prior context",
            },
            {
                "id": "quiz-request",
                "role": "user",
                "content": "Quiz me on this",
            },
        ]

        prior = chats_api._prior_quiz_conversation(
            conversation,
            "quiz-request",
        )

        self.assertEqual(prior, conversation[:1])


class QuizSourceMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_source_message_is_reused_without_a_second_write(
        self,
    ) -> None:
        append_message = AsyncMock()
        with patch.object(
            chats_api.crud,
            "append_conversation_message_if_missing",
            new=append_message,
        ):
            source_message_id = await chats_api._ensure_quiz_source_message(
                chat_id="chat-1",
                user_id="user-1",
                question="Make me a quiz",
                conversation=[
                    {
                        "id": "message-1",
                        "role": "user",
                        "content": "Make me a quiz",
                    }
                ],
                request_message_id="message-1",
                config=None,
            )

        self.assertEqual(source_message_id, "message-1")
        append_message.assert_not_awaited()

    async def test_new_source_message_keeps_the_frontend_message_id(self) -> None:
        append_message = AsyncMock(
            return_value={
                "id": "chat-1",
                "conversation": [
                    {
                        "id": "message-from-browser",
                        "role": "user",
                        "content": "Make me a quiz",
                    }
                ],
            }
        )
        with patch.object(
            chats_api.crud,
            "append_conversation_message_if_missing",
            new=append_message,
        ):
            source_message_id = await chats_api._ensure_quiz_source_message(
                chat_id="chat-1",
                user_id="user-1",
                question="Make me a quiz",
                conversation=[],
                request_message_id="message-from-browser",
                config=None,
            )

        self.assertEqual(source_message_id, "message-from-browser")
        append_message.assert_awaited_once_with(
            chat_id="chat-1",
            user_id="user-1",
            message={
                "id": "message-from-browser",
                "role": "user",
                "content": "Make me a quiz",
            },
        )


if __name__ == "__main__":
    unittest.main()
