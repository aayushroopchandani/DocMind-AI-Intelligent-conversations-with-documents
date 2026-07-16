"""Backend-only quiz submission and deterministic evaluation endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from apis.deps import current_user_id, verify_internal_secret
from db import crud
from db.models.attempt_quiz import (
    QuizAttemptInDB,
    QuizAttemptSubmission,
)
from db.models.generated_quiz import GeneratedQuizInDB
from services.quiz_evaluator import (
    QuizDefinitionError,
    QuizSubmissionError,
    evaluate_quiz,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quizzes", tags=["quiz-attempts"])


@router.post(
    "/{quiz_id}/attempts",
    response_model=QuizAttemptInDB,
    response_model_by_alias=False,
)
async def submit_quiz_attempt(
    quiz_id: str,
    body: QuizAttemptSubmission,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> QuizAttemptInDB:
    """Evaluate and persist one attempt against the authoritative stored quiz."""
    try:
        stored_quiz = await crud.get_generated_quiz(
            quiz_id=quiz_id,
            user_id=user_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if stored_quiz is None:
        raise HTTPException(status_code=404, detail="Generated quiz not found")

    try:
        quiz = GeneratedQuizInDB.model_validate(stored_quiz)
    except ValidationError as exc:
        logger.exception("Stored generated quiz %s is invalid", quiz_id)
        raise HTTPException(
            status_code=500,
            detail="The stored quiz cannot be evaluated.",
        ) from exc

    try:
        attempt = evaluate_quiz(
            quiz_id=quiz_id,
            quiz=quiz,
            submission=body,
        )
    except QuizSubmissionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except QuizDefinitionError as exc:
        logger.exception("Generated quiz %s cannot be evaluated", quiz_id)
        raise HTTPException(
            status_code=500,
            detail="The stored quiz cannot be evaluated.",
        ) from exc

    try:
        stored_attempt = await crud.create_quiz_attempt(attempt=attempt)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return QuizAttemptInDB.model_validate(stored_attempt)
