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


class QuizQuestionTopic(BaseModel):
    main_topic: str = Field(
        ...,
        min_length=1,
        description=(
            "Canonical broad topic tested by this question. Reuse the exact same "
            "name, spelling, and capitalization for questions on the same topic; "
            "do not create variants by adding acronyms or aliases."
        ),
    )
    sub_topics: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Canonical specific concepts tested by this question. Use concise "
            "names and reuse their exact spelling and capitalization everywhere "
            "the same concepts appear in the quiz."
        ),
    )


class QuizQuestionBase(BaseModel):
    id: str = Field(..., description="Stable question id, e.g. q1")
    topic: QuizQuestionTopic = Field(
        ...,
        description=(
            "Topic classification used to aggregate question performance in quiz "
            "results."
        ),
    )
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


class PlayableQuizQuestionBase(BaseModel):
    """Question metadata safe to reveal before an attempt is submitted."""

    id: str
    topic: QuizQuestionTopic


class PlayableSingleCorrectMCQQuestion(PlayableQuizQuestionBase):
    type: Literal["single_correct_mcq"] = "single_correct_mcq"
    question: str
    options: MCQOptions


class PlayableMultipleCorrectMCQQuestion(PlayableQuizQuestionBase):
    type: Literal["multiple_correct_mcq"] = "multiple_correct_mcq"
    question: str
    options: MCQOptions


class PlayableTrueFalseQuestion(PlayableQuizQuestionBase):
    type: Literal["true_false"] = "true_false"
    statement: str


class PlayableBlank(BaseModel):
    blank_id: str


class PlayableFillInTheBlankQuestion(PlayableQuizQuestionBase):
    type: Literal["fill_in_the_blank"] = "fill_in_the_blank"
    question: str
    blanks: list[PlayableBlank] = Field(default_factory=list)


class PlayableMatchTheFollowingQuestion(PlayableQuizQuestionBase):
    type: Literal["match_the_following"] = "match_the_following"
    question: str
    left_items: list[MatchItem] = Field(default_factory=list)
    right_items: list[MatchItem] = Field(default_factory=list)


PlayableGeneratedQuizQuestion = Union[
    PlayableSingleCorrectMCQQuestion,
    PlayableMultipleCorrectMCQQuestion,
    PlayableTrueFalseQuestion,
    PlayableFillInTheBlankQuestion,
    PlayableMatchTheFollowingQuestion,
]


class GeneratedQuizBase(BaseModel):
    user_id: str
    chat_id: str
    doc_ids: list[str] = Field(default_factory=list)
    source_message_id: Optional[str] = Field(
        default=None,
        description="User conversation message that requested this quiz",
    )
    response_message_id: Optional[str] = Field(
        default=None,
        description="Assistant conversation message that presents this quiz",
    )

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


class PlayableGeneratedQuiz(BaseModel):
    """Owned quiz definition with solutions and explanations removed."""

    id: str
    source_message_id: Optional[str] = None
    response_message_id: Optional[str] = None
    quiz_scope: QuizScope
    target: Optional[str] = None
    mode: Optional[QuizMode] = None
    number_of_questions: int = Field(ge=1, le=20)
    difficulty: QuizDifficulty
    question_formats: list[QuizQuestionFormat] = Field(default_factory=list)
    status: QuizStatus
    questions: list[PlayableGeneratedQuizQuestion] = Field(default_factory=list)


def to_playable_generated_quiz(quiz: GeneratedQuizInDB) -> PlayableGeneratedQuiz:
    """Redact solutions, explanations, and citations from a stored quiz."""
    if not quiz.id:
        raise ValueError("A playable quiz requires a persisted id.")
    playable_questions: list[PlayableGeneratedQuizQuestion] = []
    model_by_type = {
        "single_correct_mcq": PlayableSingleCorrectMCQQuestion,
        "multiple_correct_mcq": PlayableMultipleCorrectMCQQuestion,
        "true_false": PlayableTrueFalseQuestion,
        "fill_in_the_blank": PlayableFillInTheBlankQuestion,
        "match_the_following": PlayableMatchTheFollowingQuestion,
    }
    for question in quiz.questions:
        playable_questions.append(
            model_by_type[question.type].model_validate(question.model_dump())
        )

    return PlayableGeneratedQuiz(
        id=quiz.id,
        source_message_id=quiz.source_message_id,
        response_message_id=quiz.response_message_id,
        quiz_scope=quiz.quiz_scope,
        target=quiz.target,
        mode=quiz.mode,
        number_of_questions=quiz.number_of_questions,
        difficulty=quiz.difficulty,
        question_formats=quiz.question_formats,
        status=quiz.status,
        questions=playable_questions,
    )
