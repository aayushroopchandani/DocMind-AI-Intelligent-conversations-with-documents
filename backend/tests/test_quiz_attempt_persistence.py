from __future__ import annotations

import unittest
from unittest.mock import patch

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from db import crud
from db.models.attempt_quiz import QuizAttemptCreate


class _InsertResult:
    def __init__(self) -> None:
        self.inserted_id = ObjectId()


class _QuizAttemptsCollection:
    def __init__(self) -> None:
        self.insert_calls = 0
        self.inserted: dict | None = None

    async def find_one(self, *_args, **_kwargs):
        if self.insert_calls == 0:
            return None
        return {"attempt_number": 1}

    async def insert_one(self, document):
        self.insert_calls += 1
        if self.insert_calls == 1:
            raise DuplicateKeyError("concurrent attempt")
        self.inserted = document
        return _InsertResult()


class _Database:
    def __init__(self) -> None:
        self.quiz_attempts = _QuizAttemptsCollection()


class QuizAttemptPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_attempt_number_retries_after_concurrent_insert(self) -> None:
        database = _Database()
        attempt = QuizAttemptCreate(
            quiz_id="quiz-1",
            user_id="user-1",
            chat_id="chat-1",
            status="evaluated",
        )

        with patch("db.crud.get_db", return_value=database):
            stored = await crud.create_quiz_attempt(attempt=attempt)

        self.assertEqual(database.quiz_attempts.insert_calls, 2)
        self.assertEqual(database.quiz_attempts.inserted["attempt_number"], 2)
        self.assertEqual(stored["attempt_number"], 2)
        self.assertIn("created_at", database.quiz_attempts.inserted)
        self.assertIn("updated_at", database.quiz_attempts.inserted)


if __name__ == "__main__":
    unittest.main()
