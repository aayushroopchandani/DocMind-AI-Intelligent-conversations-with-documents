from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from .generated_quiz import (
    GeneratedQuizQuestion,
    OptionKey,
    QuizQuestionFormat,
    QuizQuestionTopic,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


QuizAttemptStatus = Literal[
    "in_progress",
    "submitted",
    "evaluated",
    "abandoned",
]
QuizAnswerStatus = Literal[
    "correct",
    "incorrect",
    "partially_correct",
    "skipped",
]
TopicPerformanceStatus = Literal[
    "weak",
    "needs_practice",
    "strong",
    "insufficient_data",
]


class BlankResponse(BaseModel):
    blank_id: str
    answer: str


class MatchResponse(BaseModel):
    left_id: str
    right_id: str


class QuizQuestionResponse(BaseModel):
    """User response fields shared by all supported quiz question formats."""

    selected_options: list[OptionKey] = Field(default_factory=list)
    boolean_answer: Optional[bool] = None
    blank_answers: list[BlankResponse] = Field(default_factory=list)
    matches: list[MatchResponse] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class QuizAnswerSubmission(BaseModel):
    """Client-controlled fields accepted when a quiz is submitted."""

    question_id: str = Field(..., min_length=1)
    response: QuizQuestionResponse = Field(default_factory=QuizQuestionResponse)
    time_taken_seconds: int = Field(default=0, ge=0, le=86_400)

    model_config = {"extra": "forbid"}


class QuizAttemptSubmission(BaseModel):
    submission_id: str = Field(
        default_factory=lambda: str(uuid4()),
        min_length=1,
        max_length=128,
    )
    answers: list[QuizAnswerSubmission] = Field(
        default_factory=list,
        max_length=20,
    )

    model_config = {"extra": "forbid"}


class QuizAnswerEvaluation(BaseModel):
    status: QuizAnswerStatus
    is_correct: bool
    awarded_marks: float = Field(default=0, ge=0)
    maximum_marks: float = Field(default=1, gt=0)


class QuizAttemptAnswer(BaseModel):
    question_id: str
    question_type: QuizQuestionFormat
    response: QuizQuestionResponse = Field(default_factory=QuizQuestionResponse)
    evaluation: Optional[QuizAnswerEvaluation] = None
    topic: QuizQuestionTopic
    time_taken_seconds: int = Field(default=0, ge=0)
    answered_at: Optional[datetime] = None


class QuizAttemptResult(BaseModel):
    score: float = Field(ge=0)
    maximum_score: float = Field(gt=0)
    percentage: float = Field(ge=0, le=100)
    correct: int = Field(default=0, ge=0)
    incorrect: int = Field(default=0, ge=0)
    partially_correct: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)


class SubTopicPerformance(BaseModel):
    name: str = Field(..., min_length=1)
    correct: int = Field(default=0, ge=0)
    attempted: int = Field(default=0, ge=0)
    total_questions: int = Field(default=0, ge=0)
    percentage: float = Field(ge=0, le=100)
    status: TopicPerformanceStatus


class MainTopicPerformance(BaseModel):
    main_topic: str = Field(..., min_length=1)
    correct: int = Field(default=0, ge=0)
    partially_correct: int = Field(default=0, ge=0)
    incorrect: int = Field(default=0, ge=0)
    attempted: int = Field(default=0, ge=0)
    total_questions: int = Field(default=0, ge=0)
    percentage: float = Field(ge=0, le=100)
    status: TopicPerformanceStatus
    sub_topics: list[SubTopicPerformance] = Field(default_factory=list)


class WeakTopic(BaseModel):
    main_topic: str = Field(..., min_length=1)
    sub_topic: str = Field(..., min_length=1)
    percentage: float = Field(ge=0, le=100)
    question_count: int = Field(ge=1)


class QuizAttemptBase(BaseModel):
    quiz_id: str
    user_id: str
    chat_id: str
    submission_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    attempt_number: int = Field(default=1, ge=1)
    status: QuizAttemptStatus = "in_progress"
    started_at: datetime = Field(default_factory=utc_now)
    submitted_at: Optional[datetime] = None
    duration_seconds: Optional[int] = Field(default=None, ge=0)
    answers: list[QuizAttemptAnswer] = Field(default_factory=list)
    result: Optional[QuizAttemptResult] = None
    topic_performance: list[MainTopicPerformance] = Field(default_factory=list)
    weak_topics: list[WeakTopic] = Field(default_factory=list)


class QuizAttemptCreate(QuizAttemptBase):
    pass


class QuizAttemptInDB(QuizAttemptBase):
    id: Optional[str] = Field(default=None, alias="_id")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"populate_by_name": True}


class EvaluatedQuizAttemptResponse(QuizAttemptInDB):
    """Persisted attempt plus transient solved questions for post-submit review."""

    review_questions: list[GeneratedQuizQuestion] = Field(default_factory=list)
