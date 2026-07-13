from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Optional, Sequence, get_args

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from config.settings import settings
from db.models.generated_quiz import (
    GeneratedQuizCreate,
    GeneratedQuizQuestion,
    QuizCitation,
    QuizDifficulty,
    QuizMode,
    QuizQuestionFormat,
)
from scripts.chat_with_pdf import (
    create_multi_query_retriever,
    get_main_llm,
    retrieve_documents,
)
from utils.format_document import format_documents_with_citations
from utils.pydantic_schemas import Citation

logger = logging.getLogger(__name__)

DEFAULT_QUESTION_FORMAT: QuizQuestionFormat = "single_correct_mcq"
MAX_CITATIONS_PER_QUESTION = 2

SUPPORTED_QUESTION_FORMATS = set(get_args(QuizQuestionFormat))
SUPPORTED_DIFFICULTIES = set(get_args(QuizDifficulty))
SUPPORTED_MODES = set(get_args(QuizMode))


class TopicBasedQuizRequest(BaseModel):
    user_id: str
    chat_id: str
    doc_ids: list[str] = Field(default_factory=list)
    document_names: dict[str, str] = Field(default_factory=dict)
    target: str
    query: str = ""
    number_of_questions: int = Field(default=5, ge=1, le=20)
    difficulty: QuizDifficulty = "medium"
    question_formats: list[QuizQuestionFormat] = Field(
        default_factory=lambda: [DEFAULT_QUESTION_FORMAT]
    )
    mode: Optional[QuizMode] = None


class TopicQuizLLMResponse(BaseModel):
    questions: list[GeneratedQuizQuestion] = Field(default_factory=list)


@lru_cache(maxsize=1)
def _get_topic_quiz_llm():
    return get_main_llm().with_structured_output(TopicQuizLLMResponse)


def _enum_value(value) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _normalize_question_formats(
    formats: Sequence[QuizQuestionFormat | str] | None,
) -> list[QuizQuestionFormat]:
    normalized: list[QuizQuestionFormat] = []
    for item in formats or []:
        value = _enum_value(item)
        if value in SUPPORTED_QUESTION_FORMATS and value not in normalized:
            normalized.append(value)  # type: ignore[arg-type]
    return normalized or [DEFAULT_QUESTION_FORMAT]


def _normalize_difficulty(value: QuizDifficulty | str | None) -> QuizDifficulty:
    normalized = _enum_value(value)
    if normalized in SUPPORTED_DIFFICULTIES:
        return normalized  # type: ignore[return-value]
    return "medium"


def _normalize_mode(value: QuizMode | str | None) -> QuizMode | None:
    normalized = _enum_value(value)
    if normalized in SUPPORTED_MODES:
        return normalized  # type: ignore[return-value]
    return None


def _retrieval_query(target: str, query: str) -> str:
    query = query.strip()
    target = target.strip()
    if query and target.lower() not in query.lower():
        return f"{query}\n\nFocus topic: {target}"
    return query or target


def _to_quiz_citation(citation: Citation) -> QuizCitation:
    return QuizCitation(
        document_id=citation.document_id,
        document_name=citation.document_name,
        page_number=citation.page_number,
        chunk_id=citation.chunk_id,
        excerpt=citation.excerpt,
    )


def _citation_key(citation: QuizCitation) -> tuple[str, int | None, str | None]:
    return (citation.document_id, citation.page_number, citation.chunk_id)


def _valid_question_citations(
    citations: list[QuizCitation],
    allowed_citations: list[QuizCitation],
) -> list[QuizCitation]:
    by_chunk_id = {
        citation.chunk_id: citation
        for citation in allowed_citations
        if citation.chunk_id
    }
    by_doc_page = {
        (citation.document_id, citation.page_number): citation
        for citation in allowed_citations
    }

    selected: list[QuizCitation] = []
    seen: set[tuple[str, int | None, str | None]] = set()

    for citation in citations:
        allowed = None
        if citation.chunk_id:
            allowed = by_chunk_id.get(citation.chunk_id)
        if allowed is None:
            allowed = by_doc_page.get((citation.document_id, citation.page_number))
        if allowed is None:
            continue

        key = _citation_key(allowed)
        if key in seen:
            continue
        seen.add(key)
        selected.append(allowed.model_copy(deep=True))
        if len(selected) >= MAX_CITATIONS_PER_QUESTION:
            break

    if selected:
        return selected

    return [
        citation.model_copy(deep=True)
        for citation in allowed_citations[:MAX_CITATIONS_PER_QUESTION]
    ]


def _post_process_questions(
    questions: list[GeneratedQuizQuestion],
    allowed_citations: list[QuizCitation],
    number_of_questions: int,
) -> list[GeneratedQuizQuestion]:
    processed: list[GeneratedQuizQuestion] = []

    for index, question in enumerate(questions[:number_of_questions], start=1):
        question.id = f"q{index}"
        question.citations = _valid_question_citations(
            question.citations,
            allowed_citations,
        )
        processed.append(question)

    return processed


def _system_prompt() -> str:
    return (
        "You generate document-grounded quiz questions for a PDF chat app. "
        "Use only the retrieved context. Do not use outside knowledge. "
        "Return the structured output exactly matching the provided schema. "
        "Every question must be answerable from the context and must include "
        "one or two citations copied from the allowed citation sources. "
        "For MCQ answers, the answer text must exactly match the selected option. "
        "For multiple-correct MCQs, include every correct option and no incorrect options. "
        "For fill-in-the-blank, use clear blanks and accepted answer variants. "
        "For match-the-following, keep left and right ids stable and unambiguous."
    )


def _human_prompt(
    *,
    request: TopicBasedQuizRequest,
    context: str,
    allowed_citations: list[QuizCitation],
) -> str:
    citation_payload = [
        citation.model_dump(mode="json") for citation in allowed_citations
    ]
    return (
        "Generate the quiz from this topic-based request.\n\n"
        f"Topic target: {request.target}\n"
        f"Original user query: {request.query or request.target}\n"
        f"Number of questions: {request.number_of_questions}\n"
        f"Difficulty: {request.difficulty}\n"
        f"Question formats: {', '.join(request.question_formats)}\n"
        f"Mode: {request.mode or 'none'}\n\n"
        "Rules:\n"
        "- Generate exactly the requested number of questions.\n"
        "- Use the requested question formats; if multiple formats are requested, "
        "mix them evenly.\n"
        "- Prefer conceptual understanding over copy-paste recall.\n"
        "- Keep explanations short and grounded in the cited context.\n"
        "- Copy citation fields only from the allowed citation sources below.\n\n"
        "Allowed citation sources JSON:\n"
        f"{json.dumps(citation_payload, ensure_ascii=False)}\n\n"
        "Retrieved context:\n"
        f"{context}"
    )


async def generate_topic_based_quiz(
    *,
    user_id: str,
    chat_id: str,
    doc_ids: list[str],
    target: str,
    document_names: dict[str, str] | None = None,
    query: str = "",
    number_of_questions: int | None = None,
    difficulty: QuizDifficulty | str | None = None,
    question_formats: Sequence[QuizQuestionFormat | str] | None = None,
    mode: QuizMode | str | None = None,
) -> GeneratedQuizCreate:
    """
    Generate a topic-based quiz from retrieved document chunks.

    This function is intentionally persistence-free. Callers can return the
    quiz immediately, then store it asynchronously without blocking the stream.
    """
    cleaned_target = target.strip()
    if not cleaned_target:
        raise ValueError("A topic-based quiz requires a target topic.")
    if not doc_ids:
        raise ValueError("A topic-based quiz requires at least one document.")

    request = TopicBasedQuizRequest(
        user_id=user_id,
        chat_id=chat_id,
        doc_ids=doc_ids,
        document_names=document_names or {},
        target=cleaned_target,
        query=query.strip(),
        number_of_questions=number_of_questions or 5,
        difficulty=_normalize_difficulty(difficulty),
        question_formats=_normalize_question_formats(question_formats),
        mode=_normalize_mode(mode),
    )

    retriever = create_multi_query_retriever(request.user_id, request.doc_ids)
    documents = await retrieve_documents(
        retriever,
        _retrieval_query(request.target, request.query),
    )
    if not documents:
        raise ValueError(
            f'Could not find relevant chunks for topic "{request.target}".'
        )

    context, rag_citations = format_documents_with_citations(
        documents,
        document_names=request.document_names,
        max_context_tokens=settings.retrieval_max_context_tokens,
    )
    if not context or not rag_citations:
        raise ValueError(
            f'Could not prepare quiz context for topic "{request.target}".'
        )

    allowed_citations = [_to_quiz_citation(citation) for citation in rag_citations]
    llm_response: TopicQuizLLMResponse = await _get_topic_quiz_llm().ainvoke(
        [
            SystemMessage(content=_system_prompt()),
            HumanMessage(
                content=_human_prompt(
                    request=request,
                    context=context,
                    allowed_citations=allowed_citations,
                )
            ),
        ]
    )

    questions = _post_process_questions(
        llm_response.questions,
        allowed_citations,
        request.number_of_questions,
    )
    if not questions:
        raise RuntimeError("The model did not generate any quiz questions.")

    return GeneratedQuizCreate(
        user_id=request.user_id,
        chat_id=request.chat_id,
        doc_ids=request.doc_ids,
        quiz_scope="topic_based",
        target=request.target,
        mode=request.mode,
        number_of_questions=request.number_of_questions,
        difficulty=request.difficulty,
        question_formats=request.question_formats,
        status="generated",
        questions=questions,
    )
