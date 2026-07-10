from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class IntentType(str, Enum):
    GENERAL_QA = "general_qa"
    SUMMARIZATION = "summarization"
    QUIZ = "quiz"


class QuizScope(str, Enum):
    CONTEXT_BASED = "context_based"
    TOPIC_BASED = "topic_based"
    STRUCTURE_BASED = "structure_based"
    WHOLE_DOCUMENT = "whole_document"


class QuizQuestionFormat(str, Enum):
    SINGLE_CORRECT_MCQ = "single_correct_mcq"
    MULTIPLE_CORRECT_MCQ = "multiple_correct_mcq"
    TRUE_FALSE = "true_false"
    FILL_IN_THE_BLANK = "fill_in_the_blank"
    MATCH_THE_FOLLOWING = "match_the_following"


class QuizDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class IntentDocument(BaseModel):
    document_id: str
    document_name: str


class DetectedIntent(BaseModel):
    intent: IntentType = IntentType.GENERAL_QA
    doc_ids: list[str] = Field(default_factory=list)
    target: str | None = None
    quiz_scope: QuizScope | None = None
    question_formats: list[QuizQuestionFormat] = Field(default_factory=list)
    difficulty: QuizDifficulty | None = None
    number_of_questions: int | None = Field(default=None, ge=1, le=20)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class LLMIntentResponse(BaseModel):
    intent: IntentType = Field(
        default=IntentType.GENERAL_QA,
        description=(
            "general_qa, summarization, or quiz. Use quiz when the user asks "
            "to generate quiz questions or be quizzed."
        ),
    )
    doc_ids: list[str] = Field(
        default_factory=list,
        description="Subset of available document_id values. Empty means use the selected documents.",
    )
    target: str | None = Field(
        default=None,
        description=(
            "For summarization, the specific section/chapter/topic to summarize. "
            "For quiz, the topic or document structure target. Null for context-based "
            "or whole-document quiz requests."
        ),
    )
    quiz_scope: QuizScope | None = Field(
        default=None,
        description=(
            "Quiz scope only when intent is quiz: context_based, topic_based, "
            "structure_based, or whole_document."
        ),
    )
    question_formats: list[QuizQuestionFormat] = Field(
        default_factory=list,
        description="Quiz question formats requested by the user. Empty means use single_correct_mcq.",
    )
    difficulty: QuizDifficulty | None = Field(
        default=None,
        description="Quiz difficulty. Null means use medium.",
    )
    number_of_questions: int | None = Field(
        default=None,
        description="Requested quiz question count. Values are normalized to 1 to 20. Null means use 5.",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
