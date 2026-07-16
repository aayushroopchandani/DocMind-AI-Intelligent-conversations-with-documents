from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from apis import chats as chats_api
from scripts.intent_detection import DetectedIntent, IntentType
from utils.pydantic_schemas import StreamAskRequest


class SummaryConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_summary_is_saved_to_chat_conversation(self) -> None:
        async def summary_events(**kwargs):
            del kwargs
            yield {"type": "token", "content": "Saved summary"}
            yield {
                "type": "final",
                "data": {
                    "answer": "Saved summary",
                    "answer_found": True,
                    "status": "complete",
                    "document_contributions": [],
                    "citations": [],
                    "confidence_score": 0.85,
                    "follow_up_questions": [],
                },
            }
            yield {"type": "done"}

        save_messages = AsyncMock()
        with (
            patch.object(
                chats_api.crud,
                "get_chat",
                new=AsyncMock(
                    return_value={
                        "id": "chat_id",
                        "user_id": "user_id",
                        "doc_ids": ["mongo_document_id"],
                        "conversation": [],
                    }
                ),
            ),
            patch.object(
                chats_api.crud,
                "get_documents_by_ids",
                new=AsyncMock(
                    return_value=[
                        {
                            "document_id": "document_hash",
                            "filename": "document.pdf",
                            "ingestion_status": "ready",
                        }
                    ]
                ),
            ),
            patch.object(
                chats_api,
                "detect_intent",
                new=AsyncMock(
                    return_value=DetectedIntent(
                        intent=IntentType.SUMMARIZATION,
                        doc_ids=["document_hash"],
                        target="Section One",
                        confidence=1.0,
                    )
                ),
            ),
            patch.object(
                chats_api,
                "stream_level1_pdf_with_outline",
                new=summary_events,
            ),
            patch.object(
                chats_api,
                "save_chat_messages",
                new=save_messages,
            ),
        ):
            response = await chats_api.stream_chat(
                chat_id="chat_id",
                body=StreamAskRequest(
                    question="Summarize Section One",
                    document_ids=["document_hash"],
                ),
                user_id="user_id",
                _=None,
            )
            frames = []
            async for frame in response.body_iterator:
                frames.append(frame.decode() if isinstance(frame, bytes) else frame)

        save_messages.assert_awaited_once()
        request, answer, saved_response = save_messages.await_args.args[:3]
        self.assertEqual(request.chat_id, "chat_id")
        self.assertEqual(request.question, "Summarize Section One")
        self.assertEqual(answer, "Saved summary")
        self.assertEqual(saved_response.answer, "Saved summary")

        events = [
            json.loads(frame.removeprefix("data: ").strip())
            for frame in frames
        ]
        self.assertEqual(events[-1]["type"], "done")


if __name__ == "__main__":
    unittest.main()
