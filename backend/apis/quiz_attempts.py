"""Backend-only quiz submission and deterministic evaluation endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from apis.deps import current_user_id, verify_internal_secret
from db import crud
from db.models.attempt_quiz import (
    EvaluatedQuizAttemptResponse,
    QuizAttemptInDB,
    QuizAttemptSubmission,
)
from db.models.generated_quiz import (
    GeneratedQuizInDB,
    PlayableGeneratedQuiz,
    to_playable_generated_quiz,
)
from services.quiz_evaluator import (
    QuizDefinitionError,
    QuizSubmissionError,
    evaluate_quiz,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quizzes", tags=["quiz-attempts"])


@router.get(
    "/{quiz_id}",
    response_model=PlayableGeneratedQuiz,
    response_model_by_alias=False,
)
async def get_generated_quiz(
    quiz_id: str,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> PlayableGeneratedQuiz:
    """Return a generated quiz owned by the authenticated user."""
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
        return to_playable_generated_quiz(quiz)
    except (ValidationError, ValueError) as exc:
        logger.exception("Stored generated quiz %s is invalid", quiz_id)
        raise HTTPException(
            status_code=500,
            detail="The stored quiz is invalid.",
        ) from exc


@router.post(
    "/{quiz_id}/attempts",
    response_model=EvaluatedQuizAttemptResponse,
    response_model_by_alias=False,
)
async def submit_quiz_attempt(
    quiz_id: str,
    body: QuizAttemptSubmission,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> EvaluatedQuizAttemptResponse:
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
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    persisted_attempt = QuizAttemptInDB.model_validate(stored_attempt)
    return EvaluatedQuizAttemptResponse(
        **persisted_attempt.model_dump(),
        review_questions=[
            question.model_copy(deep=True) for question in quiz.questions
        ],
    )
