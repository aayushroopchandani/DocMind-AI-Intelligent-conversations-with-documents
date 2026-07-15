from __future__ import annotations

import json
import os
import re
from functools import lru_cache

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .schemas import (
    DetectedIntent,
    IntentDocument,
    IntentType,
    LLMIntentResponse,
    MentionStatus,
    QuizDifficulty,
    QuizMode,
    QuizQuestionFormat,
    QuizScope,
)

load_dotenv()

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
COUNT_NUMBER_TOKEN_PATTERN = r"\d+|" + "|".join(NUMBER_WORDS)
STRUCTURE_NUMBER_TOKEN_PATTERN = r"\d+(?:\.\d+)*|" + "|".join(NUMBER_WORDS)

SUMMARY_PATTERN = re.compile(
    r"\b("
    r"summarize|summarise|summary|short summary|brief summary|"
    r"give me a summary|tl;dr|tldr|overview|recap|"
    r"key points|main points|important points|"
    r"chapter summary|section summary"
    r")\b",
    re.IGNORECASE,
)
QUIZ_PATTERN = re.compile(
    r"\b(?:quiz(?:zes)?|quiz\s+me|test\s+me|practice\s+questions?|"
    r"question\s+set|flashcards?)\b"
    r"|"
    r"\b(?:create|generate|make|write|prepare|build|give\s+me)\s+"
    r"(?:a\s+|an\s+|some\s+|[a-z0-9-]+\s+)?"
    r"(?:quiz|questions?|mcqs?|true\s*(?:/|or)\s*false|"
    r"fill[-\s]?in[-\s]?the[-\s]?blank|match(?:ing)?(?:\s+the\s+following)?)\b"
    r"|"
    r"\b(?:create|generate|make|write|prepare|build|give\s+me)\b.{0,120}"
    r"\b(?:quiz|questions?|mcqs?)\b"
    r"|"
    r"\bask\s+me\s+(?:questions?|mcqs?)\b",
    re.IGNORECASE,
)
WHOLE_DOCUMENT_PATTERN = re.compile(
    r"\b(?:whole|entire|full)\s+(?:pdf|document|doc)\b"
    r"|\b(?:all|everything)\s+(?:in|from)\s+(?:the\s+)?(?:pdf|document|doc)\b"
    r"|\bcovering\s+everything\s*(?:[.?!,;:]|$)",
    re.IGNORECASE,
)
STRUCTURE_NUMBER_PATTERN = re.compile(
    rf"\b(chapter|chap\.?|section|sec\.?|part|unit|module)\s+"
    rf"({STRUCTURE_NUMBER_TOKEN_PATTERN}|[ivxlcdm]+)(?:\b|$)",
    re.IGNORECASE,
)
STRUCTURE_NAME_PATTERN = re.compile(
    r"\b(?:from|on|about|for)\s+(?:the\s+)?"
    r"(introduction|abstract|preface|conclusion|appendix|references|"
    r"bibliography|glossary|methodology|methods|results|discussion)"
    r"\s*(?:[.?!,;:]|$)",
    re.IGNORECASE,
)
STRUCTURE_LABELS = {
    "chap": "chapter",
    "chap.": "chapter",
    "chapter": "chapter",
    "sec": "section",
    "sec.": "section",
    "section": "section",
    "part": "part",
    "unit": "unit",
    "module": "module",
}
STRUCTURE_NAMES = {
    "introduction",
    "abstract",
    "preface",
    "conclusion",
    "appendix",
    "references",
    "bibliography",
    "glossary",
    "methodology",
    "methods",
    "results",
    "discussion",
}
TOPIC_TARGET_PATTERNS = (
    re.compile(r"\b(?:on|about|regarding|over|covering)\s+(.+)$", re.IGNORECASE),
    re.compile(r"\b(?:from|for)\s+(.+)$", re.IGNORECASE),
)
CONTEXT_REFERENCE_TARGET_PATTERN = re.compile(
    r"^(?:"
    r"(?:(?:both|all)\s+of\s+)?(?:this|that|these|those|it|them)"
    r"(?:\s+(?:topics?|concepts?|points?|things?|sections?|answers?|"
    r"explanations?|ones))?"
    r"|(?:the\s+)?(?:above|previous|last|earlier)"
    r"(?:\s+(?:topics?|discussion|answer|explanation|messages?|concepts?))?"
    r"|what\s+we\s+just\s+discussed"
    r"|we\s+just\s+discussed"
    r")$",
    re.IGNORECASE,
)
COUNT_PATTERNS = (
    re.compile(
        rf"\b({COUNT_NUMBER_TOKEN_PATTERN})\s*[- ]\s*"
        r"(?:questions?|mcqs?|items?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b({COUNT_NUMBER_TOKEN_PATTERN})\s+"
        r"(?:true\s*(?:/|or)\s*false|fill[-\s]?in[-\s]?the[-\s]?blank|matching)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b({COUNT_NUMBER_TOKEN_PATTERN})\b"
        r"(?=[\s\w/-]{0,120}\b(?:questions?|mcqs?|items?)\b)",
        re.IGNORECASE,
    ),
)
EXPLICIT_SINGLE_FORMAT_PATTERN = re.compile(
    r"\b(?:single[-\s]?correct|single[-\s]?answer|single[-\s]?choice|"
    r"one\s+correct|multiple[-\s]?choice)\b",
    re.IGNORECASE,
)
BARE_MCQ_PATTERN = re.compile(r"\bmcqs?\b", re.IGNORECASE)
MULTIPLE_CORRECT_FORMAT_PATTERN = re.compile(
    r"\b(?:multiple[-\s]?correct|multi[-\s]?correct|multiple\s+answers?|"
    r"more\s+than\s+one\s+(?:answer|correct)|select\s+all(?:\s+that\s+apply)?)\b",
    re.IGNORECASE,
)
TRUE_FALSE_FORMAT_PATTERN = re.compile(
    r"\b(?:true\s*(?:/|or)\s*false|true[-\s]?false|t\s*/\s*f)\b",
    re.IGNORECASE,
)
FILL_BLANK_FORMAT_PATTERN = re.compile(
    r"\b(?:fill[-\s]?in[-\s]?the[-\s]?blank|fill\s+in\s+the\s+blanks|"
    r"cloze)\b",
    re.IGNORECASE,
)
MATCH_FORMAT_PATTERN = re.compile(
    r"\b(?:match\s+the\s+following|matching|match\s+columns?)\b",
    re.IGNORECASE,
)
MODE_PATTERNS = (
    (
        QuizMode.RAPID_FIRE,
        re.compile(r"\b(?:rapid[-\s]?fire|quick[-\s]?fire)\b", re.IGNORECASE),
    ),
    (
        QuizMode.EXAM_MODE,
        re.compile(
            r"\b(?:exam\s+mode|exam[-\s]?style|mock\s+exam|test\s+mode)\b",
            re.IGNORECASE,
        ),
    ),
    (
        QuizMode.PRACTICE,
        re.compile(
            r"\b(?:practice\s+mode|practice\s+(?:quiz|questions?|mcqs?))\b",
            re.IGNORECASE,
        ),
    ),
)
DIFFICULTY_PATTERNS = (
    (
        QuizDifficulty.EASY,
        re.compile(r"\b(?:easy|simple|basic|beginner)\b", re.IGNORECASE),
    ),
    (
        QuizDifficulty.MEDIUM,
        re.compile(r"\b(?:medium|moderate|intermediate)\b", re.IGNORECASE),
    ),
    (
        QuizDifficulty.HARD,
        re.compile(r"\b(?:hard|difficult|advanced|challenging)\b", re.IGNORECASE),
    ),
)
QUIZ_TARGET_CLEANUP_PATTERNS = (
    re.compile(r"\bwith\s+.+$", re.IGNORECASE),
    re.compile(r"\b(?:as|using)\s+.+(?:format|questions?|mcqs?)$", re.IGNORECASE),
    re.compile(
        r"\b(?:easy|simple|basic|beginner|medium|moderate|intermediate|hard|"
        r"difficult|advanced|challenging)\s*$",
        re.IGNORECASE,
    ),
)
TARGET_PATTERNS = (
    (re.compile(r"\b(node[_-]?\d+)\b", re.IGNORECASE), False),
    (re.compile(r"\b(chapter|section|topic|part)\s+(.+)$", re.IGNORECASE), True),
    (re.compile(r"\b(?:of|about|on|for)\s+(.+)$", re.IGNORECASE), False),
    (re.compile(r"\b(?:summarize|summarise)\s+(.+)$", re.IGNORECASE), False),
)


@lru_cache(maxsize=1)
def _get_intent_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model="google/gemini-2.5-flash-lite",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=0,
    )


def _general_qa(selected_doc_ids: list[str]) -> DetectedIntent:
    return DetectedIntent(
        intent=IntentType.GENERAL_QA,
        doc_ids=selected_doc_ids,
        target=None,
        confidence=1.0,
    )


def _clean_target(value: str | None) -> str | None:
    if not value:
        return None
    target = re.sub(r"\s+", " ", value).strip(" .,:;-\"'")
    if _looks_like_context_reference_target(target):
        return None
    target = re.sub(r"^(?:the|this|that)\s+", "", target, flags=re.IGNORECASE)
    if not target:
        return None
    if _looks_like_context_reference_target(target):
        return None
    if target.lower() in {
        "this",
        "it",
        "document",
        "this document",
        "the document",
        "pdf",
        "whole document",
        "entire document",
        "all documents",
        "all docs",
        "documents",
        "docs",
        "pdfs",
        "above",
        "the above",
        "above explanation",
        "the above explanation",
        "previous explanation",
        "previous answer",
        "last answer",
        "what we just discussed",
        "we just discussed",
        "earlier",
    }:
        return None
    return target


def _looks_like_context_reference_target(value: str | None) -> bool:
    if not value:
        return False
    normalized = re.sub(r"\s+", " ", value).strip(" .,:;-\"'").lower()
    if not normalized:
        return False
    return bool(CONTEXT_REFERENCE_TARGET_PATTERN.fullmatch(normalized))


def _parse_number(value: str) -> int | None:
    normalized = value.lower().strip()
    if normalized.isdigit():
        return int(normalized)
    return NUMBER_WORDS.get(normalized)


def _normalize_number_of_questions(value: int | None) -> int:
    if value is None:
        return 5
    return min(20, max(1, value))


def _normalize_mention_status(
    status: MentionStatus | str | None,
    *,
    fallback_mentioned: bool,
) -> MentionStatus:
    if isinstance(status, MentionStatus):
        return status
    if isinstance(status, str):
        normalized = status.lower().strip().replace(" ", "_")
        try:
            return MentionStatus(normalized)
        except ValueError:
            pass
    return (
        MentionStatus.MENTIONED
        if fallback_mentioned
        else MentionStatus.NOT_MENTIONED
    )


def _normalize_question_formats(
    formats: list[QuizQuestionFormat] | None,
) -> list[QuizQuestionFormat]:
    unique: list[QuizQuestionFormat] = []
    for raw_format in formats or []:
        try:
            question_format = (
                raw_format
                if isinstance(raw_format, QuizQuestionFormat)
                else QuizQuestionFormat(raw_format)
            )
        except ValueError:
            continue
        if question_format not in unique:
            unique.append(question_format)
    return unique or [QuizQuestionFormat.SINGLE_CORRECT_MCQ]


def _normalize_difficulty(
    difficulty: QuizDifficulty | None,
) -> QuizDifficulty:
    if isinstance(difficulty, QuizDifficulty):
        return difficulty
    if difficulty is None:
        return QuizDifficulty.MEDIUM
    try:
        return QuizDifficulty(difficulty)
    except ValueError:
        return QuizDifficulty.MEDIUM


def _normalize_mode(mode: QuizMode | str | None) -> QuizMode | None:
    if isinstance(mode, QuizMode):
        return mode
    if mode is None:
        return None
    normalized = mode.lower().strip().replace(" ", "_").replace("-", "_")
    try:
        return QuizMode(normalized)
    except ValueError:
        return None


def _clean_quiz_topic_target(value: str | None) -> str | None:
    target = _clean_target(value)
    if target is None:
        return None

    for pattern in QUIZ_TARGET_CLEANUP_PATTERNS:
        target = pattern.sub("", target).strip(" .,:;-\"'")
    target = re.sub(
        r"^(?:the\s+)?(?:topics?|subjects?|sections?)\s+(?:of\s+)?",
        "",
        target,
        flags=re.IGNORECASE,
    )
    return _clean_target(target)


def _structure_label(label: str) -> str:
    return STRUCTURE_LABELS.get(label.lower().rstrip("."), label.lower())


def _extract_structure_target(question: str) -> str | None:
    number_match = STRUCTURE_NUMBER_PATTERN.search(question)
    if number_match:
        return _clean_target(
            f"{_structure_label(number_match.group(1))} {number_match.group(2)}"
        )

    name_match = STRUCTURE_NAME_PATTERN.search(question)
    if name_match:
        return _clean_target(name_match.group(1).lower())
    return None


def _looks_like_structure_target(target: str | None) -> bool:
    cleaned = _clean_target(target)
    if not cleaned:
        return False
    if cleaned.lower() in STRUCTURE_NAMES:
        return True
    return bool(STRUCTURE_NUMBER_PATTERN.fullmatch(cleaned))


def _extract_topic_target(question: str) -> str | None:
    for pattern in TOPIC_TARGET_PATTERNS:
        match = pattern.search(question)
        if match:
            target = _clean_quiz_topic_target(match.group(1))
            if target:
                return target
    return None


def _extract_quiz_scope(question: str) -> tuple[QuizScope, str | None]:
    if WHOLE_DOCUMENT_PATTERN.search(question):
        return QuizScope.WHOLE_DOCUMENT, None

    structure_target = _extract_structure_target(question)
    if structure_target:
        return QuizScope.STRUCTURE_BASED, structure_target

    topic_target = _extract_topic_target(question)
    if topic_target:
        return QuizScope.TOPIC_BASED, topic_target

    return QuizScope.CONTEXT_BASED, None


def _extract_question_count_info(question: str) -> tuple[int, MentionStatus]:
    for pattern in COUNT_PATTERNS:
        match = pattern.search(question)
        if not match:
            continue
        parsed = _parse_number(match.group(1))
        if parsed is not None:
            return _normalize_number_of_questions(parsed), MentionStatus.MENTIONED
    return 5, MentionStatus.NOT_MENTIONED


def _extract_difficulty(question: str) -> QuizDifficulty:
    for difficulty, pattern in DIFFICULTY_PATTERNS:
        if pattern.search(question):
            return difficulty
    return QuizDifficulty.MEDIUM


def _extract_question_formats_info(
    question: str,
) -> tuple[list[QuizQuestionFormat], MentionStatus]:
    multiple_correct = bool(MULTIPLE_CORRECT_FORMAT_PATTERN.search(question))
    single_correct = bool(EXPLICIT_SINGLE_FORMAT_PATTERN.search(question)) or (
        bool(BARE_MCQ_PATTERN.search(question)) and not multiple_correct
    )

    formats = [
        question_format
        for question_format, detected in (
            (QuizQuestionFormat.SINGLE_CORRECT_MCQ, single_correct),
            (QuizQuestionFormat.MULTIPLE_CORRECT_MCQ, multiple_correct),
            (
                QuizQuestionFormat.TRUE_FALSE,
                bool(TRUE_FALSE_FORMAT_PATTERN.search(question)),
            ),
            (
                QuizQuestionFormat.FILL_IN_THE_BLANK,
                bool(FILL_BLANK_FORMAT_PATTERN.search(question)),
            ),
            (
                QuizQuestionFormat.MATCH_THE_FOLLOWING,
                bool(MATCH_FORMAT_PATTERN.search(question)),
            ),
        )
        if detected
    ]
    return _normalize_question_formats(formats), _normalize_mention_status(
        None,
        fallback_mentioned=bool(formats),
    )


def _extract_quiz_mode_info(question: str) -> tuple[QuizMode | None, MentionStatus]:
    for mode, pattern in MODE_PATTERNS:
        if pattern.search(question):
            return mode, MentionStatus.MENTIONED
    return None, MentionStatus.NOT_MENTIONED


def _quiz_intent(
    *,
    selected_doc_ids: list[str],
    scope: QuizScope,
    target: str | None,
    question_formats: list[QuizQuestionFormat] | None,
    question_formats_mention_status: MentionStatus | None,
    difficulty: QuizDifficulty | None,
    number_of_questions: int | None,
    number_of_questions_mention_status: MentionStatus | None,
    mode: QuizMode | None,
    mode_mention_status: MentionStatus | None,
    confidence: float,
) -> DetectedIntent:
    if scope == QuizScope.STRUCTURE_BASED and not _looks_like_structure_target(target):
        scope = QuizScope.TOPIC_BASED if target else QuizScope.CONTEXT_BASED

    if scope == QuizScope.STRUCTURE_BASED:
        normalized_target = _clean_target(target)
    elif scope == QuizScope.TOPIC_BASED:
        normalized_target = _clean_quiz_topic_target(target)
        if normalized_target is None:
            scope = QuizScope.CONTEXT_BASED
    else:
        normalized_target = None
    normalized_mode = _normalize_mode(mode)
    mode_status = _normalize_mention_status(
        mode_mention_status,
        fallback_mentioned=normalized_mode is not None,
    )
    if normalized_mode is None:
        mode_status = MentionStatus.NOT_MENTIONED

    return DetectedIntent(
        intent=IntentType.QUIZ,
        doc_ids=selected_doc_ids,
        target=normalized_target,
        quiz_scope=scope,
        question_formats=_normalize_question_formats(question_formats),
        question_formats_mention_status=_normalize_mention_status(
            question_formats_mention_status,
            fallback_mentioned=bool(question_formats),
        ),
        difficulty=_normalize_difficulty(difficulty),
        number_of_questions=_normalize_number_of_questions(number_of_questions),
        number_of_questions_mention_status=_normalize_mention_status(
            number_of_questions_mention_status,
            fallback_mentioned=number_of_questions is not None,
        ),
        mode=normalized_mode,
        mode_mention_status=mode_status,
        confidence=confidence,
    )


def _heuristic_quiz_detect(
    question: str, selected_doc_ids: list[str]
) -> DetectedIntent | None:
    if not QUIZ_PATTERN.search(question):
        return None

    scope, target = _extract_quiz_scope(question)
    question_formats, question_formats_mention_status = _extract_question_formats_info(
        question
    )
    number_of_questions, number_of_questions_mention_status = (
        _extract_question_count_info(question)
    )
    mode, mode_mention_status = _extract_quiz_mode_info(question)

    return _quiz_intent(
        selected_doc_ids=selected_doc_ids,
        scope=scope,
        target=target,
        question_formats=question_formats,
        question_formats_mention_status=question_formats_mention_status,
        difficulty=_extract_difficulty(question),
        number_of_questions=number_of_questions,
        number_of_questions_mention_status=number_of_questions_mention_status,
        mode=mode,
        mode_mention_status=mode_mention_status,
        confidence=0.85,
    )


def _extract_summary_target(question: str) -> str | None:
    for pattern, include_label in TARGET_PATTERNS:
        match = pattern.search(question)
        if match:
            if include_label:
                return _clean_target(f"{match.group(1)} {match.group(2)}")
            return _clean_target(match.group(1))
    return None


def _normalize_doc_ids(doc_ids: list[str], selected_doc_ids: list[str]) -> list[str]:
    selected = set(selected_doc_ids)
    normalized = [doc_id for doc_id in doc_ids if doc_id in selected]
    return list(dict.fromkeys(normalized)) or selected_doc_ids


def _heuristic_detect(question: str, selected_doc_ids: list[str]) -> DetectedIntent | None:
    quiz = _heuristic_quiz_detect(question, selected_doc_ids)
    if quiz is not None:
        return quiz

    if not SUMMARY_PATTERN.search(question):
        return None
    return DetectedIntent(
        intent=IntentType.SUMMARIZATION,
        doc_ids=selected_doc_ids,
        target=_extract_summary_target(question),
        confidence=0.75,
    )


def _documents_payload(documents: list[IntentDocument]) -> str:
    return json.dumps(
        [
            {
                "document_id": document.document_id,
                "document_name": document.document_name,
            }
            for document in documents
        ],
        ensure_ascii=False,
    )


async def _llm_detect(
    *,
    question: str,
    selected_doc_ids: list[str],
    documents: list[IntentDocument],
) -> DetectedIntent:
    llm = _get_intent_llm().with_structured_output(LLMIntentResponse)
    result: LLMIntentResponse = await llm.ainvoke(
        [
            SystemMessage(
                content=(
                    "Classify the user's message for a PDF chat app.\n"
                    "Allowed intents:\n"
                    "- general_qa: questions, comparisons, explanations, extraction, or anything that should use normal RAG Q&A.\n"
                    "- summarization: the user asks for a summary, overview, recap, key points, or main points of document(s), chapter(s), section(s), or topic(s).\n"
                    "- quiz: the user asks to generate a quiz, be quizzed, or create questions from document/context/topic content.\n"
                    "If uncertain, choose general_qa.\n"
                    "For summarization or quiz, return doc_ids from the provided selected_doc_ids only. "
                    "If the user does not name a specific document, return selected_doc_ids. "
                    "Return target only when the user specifies a chapter, section, topic, heading, or focus area; otherwise null.\n"
                    "Quiz schema rules:\n"
                    "- quiz_scope=context_based when the user refers to previous conversation context, such as this, it, these, those, above, both of these topics, or what we just discussed.\n"
                    "- quiz_scope=topic_based when the user gives a topic but not an exact document location.\n"
                    "- quiz_scope=structure_based only for exact document structures such as Chapter 5, Section 3.2, or the introduction.\n"
                    "- If the user says something like 'the section supervised learning', treat it as topic_based with target 'supervised learning'.\n"
                    "- quiz_scope=whole_document for the whole PDF, entire document, all documents, or covering everything.\n"
                    "- question_formats may contain multiple values. If omitted, use single_correct_mcq and question_formats_mention_status=not_mentioned.\n"
                    "- number_of_questions defaults to 5 and must be 1 to 20. If the user explicitly says 5 questions, number_of_questions_mention_status=mentioned.\n"
                    "- difficulty defaults to medium.\n"
                    "- mode is practice, rapid_fire, exam_mode, or null. Use mode_mention_status=mentioned only when the user explicitly asks for a mode; otherwise use mode=null and mode_mention_status=not_mentioned."
                )
            ),
            HumanMessage(
                content=(
                    f"Selected document IDs:\n{json.dumps(selected_doc_ids)}\n\n"
                    f"Available documents:\n{_documents_payload(documents)}\n\n"
                    f"User message:\n{question}"
                )
            ),
        ]
    )
    if result.intent == IntentType.QUIZ:
        inferred_scope, inferred_target = _extract_quiz_scope(question)
        inferred_formats, inferred_formats_status = _extract_question_formats_info(
            question
        )
        inferred_count, inferred_count_status = _extract_question_count_info(question)
        inferred_mode, inferred_mode_status = _extract_quiz_mode_info(question)

        return _quiz_intent(
            selected_doc_ids=_normalize_doc_ids(result.doc_ids, selected_doc_ids),
            scope=result.quiz_scope or inferred_scope,
            target=result.target or inferred_target,
            question_formats=result.question_formats or inferred_formats,
            question_formats_mention_status=(
                result.question_formats_mention_status or inferred_formats_status
            ),
            difficulty=result.difficulty,
            number_of_questions=result.number_of_questions or inferred_count,
            number_of_questions_mention_status=(
                result.number_of_questions_mention_status or inferred_count_status
            ),
            mode=result.mode or inferred_mode,
            mode_mention_status=result.mode_mention_status or inferred_mode_status,
            confidence=result.confidence,
        )

    if result.intent != IntentType.SUMMARIZATION:
        return _general_qa(selected_doc_ids)
    return DetectedIntent(
        intent=IntentType.SUMMARIZATION,
        doc_ids=_normalize_doc_ids(result.doc_ids, selected_doc_ids),
        target=_clean_target(result.target),
        confidence=result.confidence,
    )


async def detect_intent(
    *,
    question: str,
    selected_doc_ids: list[str],
    documents: list[IntentDocument],
) -> DetectedIntent:
    selected_doc_ids = list(dict.fromkeys(selected_doc_ids))
    if not selected_doc_ids:
        return _general_qa([])

    heuristic = _heuristic_detect(question, selected_doc_ids)
    if heuristic is not None:
        return heuristic

    try:
        return await _llm_detect(
            question=question,
            selected_doc_ids=selected_doc_ids,
            documents=documents,
        )
    except Exception:
        return _general_qa(selected_doc_ids)
