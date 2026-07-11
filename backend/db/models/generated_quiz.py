from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


QuizScope = Literal[
    "context_based",
    "topic_based",
    "structure_based",
    "whole_document",
]
QuizMode = Literal["practice", "rapid_fire", "exam_mode"]
QuizDifficulty = Literal["easy", "medium", "hard"]
QuizQuestionFormat = Literal[
    "single_correct_mcq",
    "multiple_correct_mcq",
    "true_false",
    "fill_in_the_blank",
    "match_the_following",
]
QuizStatus = Literal["draft", "generating", "generated", "failed"]
OptionKey = Literal["A", "B", "C", "D"]


class QuizCitation(BaseModel):
    document_id: str = Field(..., description="SHA-256 document id")
    document_name: str = Field(..., description="Original PDF filename")
    page_number: Optional[int] = Field(default=None, description="1-based page number")
    chunk_id: Optional[str] = Field(default=None, description="Retrieved chunk id")
    excerpt: Optional[str] = Field(default=None, description="Short cited excerpt")


class MCQOptions(BaseModel):
    A: str
    B: str
    C: str
    D: str


class OptionAnswer(BaseModel):
    option: OptionKey
    answer: str


class MultipleCorrectScoring(BaseModel):
    requires_all_correct: bool = True
    allow_partial_credit: bool = False


class QuizQuestionBase(BaseModel):
    id: str = Field(..., description="Stable question id, e.g. q1")
    short_explanation: str = ""
    citations: list[QuizCitation] = Field(default_factory=list)


class SingleCorrectMCQQuestion(QuizQuestionBase):
    type: Literal["single_correct_mcq"] = "single_correct_mcq"
    question: str
    options: MCQOptions
    correct_answer: OptionAnswer


class MultipleCorrectMCQQuestion(QuizQuestionBase):
    type: Literal["multiple_correct_mcq"] = "multiple_correct_mcq"
    question: str
    options: MCQOptions
    correct_answers: list[OptionAnswer] = Field(default_factory=list)
    scoring: MultipleCorrectScoring = Field(default_factory=MultipleCorrectScoring)


class TrueFalseQuestion(QuizQuestionBase):
    type: Literal["true_false"] = "true_false"
    statement: str
    correct_answer: bool


class FillInTheBlankAnswer(BaseModel):
    blank_id: str
    correct_answers: list[str] = Field(default_factory=list)
    case_sensitive: bool = False


class FillInTheBlankQuestion(QuizQuestionBase):
    type: Literal["fill_in_the_blank"] = "fill_in_the_blank"
    question: str
    blanks: list[FillInTheBlankAnswer] = Field(default_factory=list)


class MatchItem(BaseModel):
    id: str
    text: str


class CorrectMatch(BaseModel):
    left_id: str
    right_id: str


class MatchTheFollowingQuestion(QuizQuestionBase):
    type: Literal["match_the_following"] = "match_the_following"
    question: str
    left_items: list[MatchItem] = Field(default_factory=list)
    right_items: list[MatchItem] = Field(default_factory=list)
    correct_matches: list[CorrectMatch] = Field(default_factory=list)


GeneratedQuizQuestion = Union[
    SingleCorrectMCQQuestion,
    MultipleCorrectMCQQuestion,
    TrueFalseQuestion,
    FillInTheBlankQuestion,
    MatchTheFollowingQuestion,
]


class GeneratedQuizBase(BaseModel):
    user_id: str
    chat_id: str
    doc_ids: list[str] = Field(default_factory=list)

    quiz_scope: QuizScope
    target: Optional[str] = None

    mode: Optional[QuizMode] = None
    number_of_questions: int = Field(default=5, ge=1, le=20)
    difficulty: QuizDifficulty = "medium"

    question_formats: list[QuizQuestionFormat] = Field(default_factory=list)
    status: QuizStatus = "draft"
    questions: list[GeneratedQuizQuestion] = Field(default_factory=list)


class GeneratedQuizCreate(GeneratedQuizBase):
    pass


class GeneratedQuizInDB(GeneratedQuizBase):
    id: Optional[str] = Field(default=None, alias="_id")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"populate_by_name": True}
