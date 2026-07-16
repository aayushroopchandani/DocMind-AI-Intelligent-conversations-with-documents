from __future__ import annotations

import unittest
from unittest.mock import patch

from db import crud


class _ChatsCollection:
    def __init__(self) -> None:
        self.update: dict | None = None

    async def find_one_and_update(self, _query, update, **_kwargs):
        self.update = update
        return {"conversation": update["$push"]["conversation"]["$each"]}


class _Database:
    def __init__(self) -> None:
        self.chats = _ChatsCollection()


class ChatMessageIdTests(unittest.IsolatedAsyncioTestCase):
    async def test_appended_messages_receive_unique_persisted_ids(self) -> None:
        database = _Database()
        messages = [
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Answer"},
        ]

        with patch("db.crud.get_db", return_value=database):
            await crud.append_conversation_messages(
                chat_id="507f1f77bcf86cd799439011",
                user_id="user-1",
                messages=messages,
            )

        persisted = database.chats.update["$push"]["conversation"]["$each"]
        message_ids = [message["id"] for message in persisted]
        self.assertTrue(all(message_ids))
        self.assertEqual(len(message_ids), len(set(message_ids)))


if __name__ == "__main__":
    unittest.main()
