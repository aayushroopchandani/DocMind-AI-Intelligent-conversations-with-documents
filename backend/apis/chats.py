"""Chat + PDF endpoints.

Uploads flow: browser -> Next.js proxy (verifies Clerk) -> here. We push the
file to Cloudinary (namespaced by user + chat) and store the returned asset
metadata on the chat document. LangChain/Qdrant ingestion is intentionally
left untouched for now.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from apis.deps import current_user_id, verify_internal_secret
from config.settings import settings
from db import crud
from db.models.chat import ChatMemory, ConversationMessage
from db.models.document import PdfDocument
from db.models.generated_quiz import GeneratedQuizCreate, GeneratedQuizInDB
from services.cloudinary_setup import delete_pdf, upload_pdf
from scripts.chat_with_pdf import ask_question, save_chat_messages
from scripts.intention_pipelines.quiz_pipeline.context_based import (
    generate_context_based_quiz,
)
from scripts.intention_pipelines.quiz_pipeline.topic_based import (
    generate_topic_based_quiz,
)
from scripts.intention_pipelines.summarization_pipeline.level1_pdf_with_outline import (
    stream_level1_pdf_with_outline,
)
from scripts.intention_pipelines.summarization_pipeline.summary_index import (
    SUMMARY_INDEX_VERSION,
    build_summary_index,
    initialize_pending_summary_indexes,
)
from scripts.intent_detection import (
    DetectedIntent,
    IntentDocument,
    IntentType,
    MentionStatus,
    QuizScope,
    detect_intent,
)
from scripts.ingest import delete_pdf_embeddings, ingest_pdf
from scripts.data_analysis_agent.extraction.utils.table_vector_store import (
    delete_table_vectors,
)
from utils.pydantic_schemas import (
    ChatRequest,
    DocMindResponse,
    IngestData,
    QuizGenerationConfig,
    StreamAskRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])
_background_summary_index_tasks: set[asyncio.Task] = set()


class ChatResponse(BaseModel):
    id: str
    user_id: str
    doc_ids: list[str] = Field(default_factory=list)
    documents: list[PdfDocument] = Field(default_factory=list)
    conversation: list[ConversationMessage] = Field(default_factory=list)
    memory: ChatMemory | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ChatDocumentsResponse(BaseModel):
    chat_id: str
    user_id: str
    doc_ids: list[str] = Field(default_factory=list)
    documents: list[PdfDocument] = Field(default_factory=list)


async def _get_chat_documents(
    *, chat_id: str, user_id: str
) -> tuple[dict, list[dict]]:
    chat = await crud.get_chat(chat_id=chat_id, user_id=user_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    documents = await crud.get_documents_by_ids(
        document_ids=chat.get("doc_ids", []),
        user_id=user_id,
    )
    return chat, documents


async def _to_chat_response(chat: dict) -> ChatResponse:
    doc_ids = chat.get("doc_ids", [])
    documents = await crud.get_documents_by_ids(
        document_ids=doc_ids,
        user_id=chat["user_id"],
    )
    return ChatResponse(
        id=chat["id"],
        user_id=chat["user_id"],
        doc_ids=doc_ids,
        documents=[PdfDocument(**document) for document in documents],
        conversation=[
            ConversationMessage(**message)
            for message in chat.get("conversation", [])
        ],
        memory=ChatMemory(**chat["memory"]) if chat.get("memory") else None,
        created_at=chat.get("created_at"),
        updated_at=chat.get("updated_at"),
    )


def _schedule_summary_index(*, user_id: str, document_id: str) -> None:
    """Start idempotent representative indexing without delaying upload."""
    task = asyncio.create_task(
        build_summary_index(user_id=user_id, document_id=document_id)
    )
    _background_summary_index_tasks.add(task)

    def _log_result(done_task: asyncio.Task) -> None:
        _background_summary_index_tasks.discard(done_task)
        try:
            built = done_task.result()
            if built:
                logger.info("Summary index ready for document %s", document_id)
        except Exception:
            logger.exception("Summary-index build failed for document %s", document_id)

    task.add_done_callback(_log_result)


@router.post("", response_model=ChatResponse)
async def create_chat(
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> ChatResponse:
    try:
        chat = await crud.create_chat(user_id=user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return await _to_chat_response(chat)


@router.get("/{chat_id}", response_model=ChatResponse)
async def get_chat(
    chat_id: str,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> ChatResponse:
    chat = await crud.get_chat(chat_id=chat_id, user_id=user_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return await _to_chat_response(chat)


@router.get("/{chat_id}/documents", response_model=ChatDocumentsResponse)
async def get_doc_for_chat(
    chat_id: str,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> ChatDocumentsResponse:
    chat, documents = await _get_chat_documents(chat_id=chat_id, user_id=user_id)
    return ChatDocumentsResponse(
        chat_id=chat["id"],
        user_id=chat["user_id"],
        doc_ids=chat.get("doc_ids", []),
        documents=[PdfDocument(**document) for document in documents],
    )


@router.post("/{chat_id}/pdfs", response_model=ChatResponse)
async def upload_pdfs(
    chat_id: str,
    files: list[UploadFile] = File(...),
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> ChatResponse:
    """Upload one or more PDFs to Cloudinary and attach them to the chat."""
    chat = await crud.get_chat(chat_id=chat_id, user_id=user_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    existing = chat.get("doc_ids", [])
    remaining = settings.max_pdfs_per_chat - len(existing)
    if remaining <= 0:
        raise HTTPException(
            status_code=409,
            detail=f"You can upload a maximum of {settings.max_pdfs_per_chat} PDFs in one chat.",
        )
    if len(files) > remaining:
        raise HTTPException(
            status_code=409,
            detail=f"Only {remaining} more PDF(s) can be added to this chat.",
        )

    updated = chat
    for file in files:
        filename = file.filename or "document.pdf"
        is_pdf = (file.content_type == "application/pdf") or filename.lower().endswith(
            ".pdf"
        )
        if not is_pdf:
            raise HTTPException(
                status_code=400, detail=f"'{filename}' is not a PDF. Only PDFs are allowed."
            )

        file_bytes = await file.read()
        document_id = hashlib.sha256(file_bytes).hexdigest()

        ready_document = await crud.get_ready_document(
            user_id=user_id,
            document_id=document_id,
        )
        if ready_document is not None:
            try:
                updated = await crud.attach_document_to_chat(
                    chat_id=chat_id,
                    user_id=user_id,
                    document_db_id=ready_document["id"],
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            _schedule_summary_index(user_id=user_id, document_id=document_id)
            continue

        pending_document, claimed = await crud.create_pending_document(
            user_id=user_id,
            document_id=document_id,
            filename=filename,
        )
        if not claimed:
            if pending_document.get("ingestion_status") == "ready":
                updated = await crud.attach_document_to_chat(
                    chat_id=chat_id,
                    user_id=user_id,
                    document_db_id=pending_document["id"],
                )
                _schedule_summary_index(user_id=user_id, document_id=document_id)
                continue
            raise HTTPException(
                status_code=409,
                detail=f"'{filename}' is already being processed. Please try again shortly.",
            )

        asset = None
        try:
            asset = upload_pdf(
                file_bytes, user_id=user_id, chat_id=chat_id, filename=filename
            )

            ingest_data = IngestData(
                secure_url=asset["secure_url"],
                filename=asset["filename"],
                document_id=document_id,
                user_id=user_id,
            )
            nodes = ingest_pdf(ingest_data)
            indexed_nodes = initialize_pending_summary_indexes(nodes)

            ready_document = await crud.mark_document_ready(
                document_db_id=pending_document["id"],
                user_id=user_id,
                metadata={
                    "public_id": asset["public_id"],
                    "private_id": asset["asset_id"],
                    "secure_url": asset["secure_url"],
                    "resource_type": asset["resource_type"],
                    "filename": asset["filename"],
                    "bytes": asset.get("bytes"),
                    "pages": asset.get("pages"),
                    "nodes": {
                        "nodes": indexed_nodes,
                        "ingestion_status": "not_ready",
                    },
                    "summary_index_status": "pending",
                    "summary_index_version": SUMMARY_INDEX_VERSION,
                },
            )
            _schedule_summary_index(user_id=user_id, document_id=document_id)
        except Exception as exc:  # pragma: no cover - surface upstream errors
            logger.exception("PDF ingestion failed for %s", filename)
            if asset is not None:
                try:
                    delete_pdf(
                        asset["public_id"],
                        resource_type=asset.get("resource_type", "raw"),
                    )
                except Exception:
                    logger.exception("Cloudinary cleanup failed for %s", filename)
            await crud.discard_pending_document(
                document_db_id=pending_document["id"],
                user_id=user_id,
            )
            status_code = 503 if isinstance(exc, RuntimeError) else 502
            raise HTTPException(status_code=status_code, detail=f"Upload failed: {exc}")

        try:
            updated = await crud.attach_document_to_chat(
                chat_id=chat_id,
                user_id=user_id,
                document_db_id=ready_document["id"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    return await _to_chat_response(updated)


def _sse(event: dict) -> str:
    """Format one event as a Server-Sent Events frame."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@dataclass(frozen=True)
class _ResolvedQuizConfig:
    mode: str | None
    number_of_questions: int | None
    question_formats: list[str]
    missing_fields: list[str]


def _intent_payload(intent: DetectedIntent) -> dict:
    """Return the public intent shape shared by both quiz SSE events."""
    return {
        "intent": intent.intent.value,
        "doc_ids": intent.doc_ids,
        "target": intent.target,
        "quiz_scope": intent.quiz_scope.value if intent.quiz_scope else None,
        "question_formats": [value.value for value in intent.question_formats],
        "question_formats_mention_status": (
            intent.question_formats_mention_status.value
            if intent.question_formats_mention_status
            else None
        ),
        "difficulty": intent.difficulty.value if intent.difficulty else None,
        "number_of_questions": intent.number_of_questions,
        "number_of_questions_mention_status": (
            intent.number_of_questions_mention_status.value
            if intent.number_of_questions_mention_status
            else None
        ),
        "mode": intent.mode.value if intent.mode else None,
        "mode_mention_status": (
            intent.mode_mention_status.value
            if intent.mode_mention_status
            else None
        ),
        "confidence": intent.confidence,
    }


def _resolve_quiz_config(
    intent: DetectedIntent,
    config: QuizGenerationConfig | None,
) -> _ResolvedQuizConfig:
    """Merge UI choices into fields omitted from the user's original request."""
    explicit_mode = (
        intent.mode_mention_status == MentionStatus.MENTIONED
        and intent.mode is not None
    )
    mode = (
        intent.mode.value
        if explicit_mode
        else (config.mode if config is not None else None)
    )

    explicit_count = (
        intent.number_of_questions_mention_status == MentionStatus.MENTIONED
        and intent.number_of_questions is not None
    )
    number_of_questions = (
        intent.number_of_questions
        if explicit_count
        else (config.number_of_questions if config is not None else None)
    )

    explicit_formats = (
        intent.question_formats_mention_status == MentionStatus.MENTIONED
        and bool(intent.question_formats)
    )
    question_formats = (
        [value.value for value in intent.question_formats]
        if explicit_formats
        else list(config.question_formats or []) if config is not None else []
    )

    missing_fields: list[str] = []
    if mode is None:
        missing_fields.append("mode")
    if number_of_questions is None:
        missing_fields.append("number_of_questions")
    if not question_formats:
        missing_fields.append("question_formats")

    return _ResolvedQuizConfig(
        mode=mode,
        number_of_questions=number_of_questions,
        question_formats=question_formats,
        missing_fields=missing_fields,
    )


def _prior_quiz_conversation(
    conversation: list[dict], source_message_id: str
) -> list[dict]:
    """Exclude the repeated setup request from context-reference resolution."""
    return [
        message
        for message in conversation
        if message.get("id") != source_message_id
    ]


async def _ensure_quiz_source_message(
    *,
    chat_id: str,
    user_id: str,
    question: str,
    conversation: list[dict],
    request_message_id: str | None,
    config: QuizGenerationConfig | None,
) -> str:
    """Persist the originating user message exactly once and return its ID."""
    source_message_id = (
        config.source_message_id if config and config.source_message_id else None
    ) or request_message_id or str(uuid4())
    existing = next(
        (
            message
            for message in conversation
            if message.get("id") == source_message_id
        ),
        None,
    )
    if existing is not None:
        if (
            existing.get("role") != "user"
            or str(existing.get("content", "")).strip() != question
        ):
            raise ValueError("The quiz source message does not match this request.")
        return source_message_id

    updated = await crud.append_conversation_message_if_missing(
        chat_id=chat_id,
        user_id=user_id,
        message={
            "id": source_message_id,
            "role": "user",
            "content": question,
        },
    )
    if updated is None:
        raise RuntimeError("Unable to store the quiz request message.")
    stored = next(
        (
            message
            for message in updated.get("conversation", [])
            if message.get("id") == source_message_id
        ),
        None,
    )
    if stored is None:
        raise RuntimeError("Unable to verify the quiz request message.")
    if (
        stored.get("role") != "user"
        or str(stored.get("content", "")).strip() != question
    ):
        raise ValueError("The quiz source message does not match this request.")
    return source_message_id


async def _persist_generated_quiz(quiz: GeneratedQuizCreate) -> dict:
    """Persist once and return the authoritative stored representation."""
    try:
        saved = await crud.create_generated_quiz(quiz=quiz)
    except DuplicateKeyError:
        if not quiz.source_message_id:
            raise
        saved = await crud.get_generated_quiz_by_source_message(
            chat_id=quiz.chat_id,
            user_id=quiz.user_id,
            source_message_id=quiz.source_message_id,
        )
        if saved is None:
            raise
    return GeneratedQuizInDB.model_validate(saved).model_dump(
        mode="json",
        by_alias=False,
    )


async def _ensure_quiz_response_message(quiz: dict) -> None:
    """Attach a durable assistant quiz card to the chat conversation."""
    response_message_id = quiz.get("response_message_id")
    source_message_id = quiz.get("source_message_id")
    quiz_id = quiz.get("id")
    if not response_message_id or not source_message_id or not quiz_id:
        raise RuntimeError("Stored quiz is missing its conversation linkage.")

    updated = await crud.append_conversation_message_if_missing(
        chat_id=quiz["chat_id"],
        user_id=quiz["user_id"],
        message={
            "id": response_message_id,
            "role": "assistant",
            "content": "Your quiz is ready.",
            "meta": {
                "kind": "quiz",
                "quiz_id": quiz_id,
                "quiz_mode": quiz.get("mode"),
                "source_message_id": source_message_id,
                "number_of_questions": quiz.get("number_of_questions"),
            },
        },
    )
    if updated is None:
        raise RuntimeError("Unable to store the quiz conversation message.")
    stored = next(
        (
            message
            for message in updated.get("conversation", [])
            if message.get("id") == response_message_id
        ),
        None,
    )
    if stored is None or (stored.get("meta") or {}).get("quiz_id") != quiz_id:
        raise RuntimeError("Unable to verify the quiz conversation message.")


def _quiz_sse_reference(quiz: dict) -> dict:
    """Expose only navigation metadata; quiz content is loaded via its owned GET."""
    return {
        "id": quiz["id"],
        "mode": quiz.get("mode"),
        "number_of_questions": quiz.get("number_of_questions"),
        "source_message_id": quiz.get("source_message_id"),
        "response_message_id": quiz.get("response_message_id"),
    }


@router.post("/{chat_id}/stream")
async def stream_chat(
    chat_id: str,
    body: StreamAskRequest,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> StreamingResponse:
    """
    Answer a question about the chat's PDFs, streaming SSE events:
    status / token / citations / final / error / done.
    """
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    # Ownership check: the chat (and therefore its documents) must belong to
    # the authenticated user. Qdrant filters are applied server-side too.
    chat = await crud.get_chat(chat_id=chat_id, user_id=user_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    documents = await crud.get_documents_by_ids(
        document_ids=chat.get("doc_ids", []),
        user_id=user_id,
    )
    attached = {
        document["document_id"]: document.get("filename") or "document.pdf"
        for document in documents
        if document.get("ingestion_status") == "ready"
    }
    if not attached:
        raise HTTPException(status_code=400, detail="Upload a PDF before asking questions.")

    if body.document_ids:
        unknown = [d for d in body.document_ids if d not in attached]
        if unknown:
            raise HTTPException(
                status_code=403, detail="One or more documents do not belong to this chat."
            )
        document_ids = body.document_ids
    else:
        document_ids = list(attached.keys())

    memory = chat.get("memory") or {}
    conversation = chat.get("conversation", [])

    async def event_stream():
        question = body.question.strip()
        document_names = {doc_id: attached[doc_id] for doc_id in document_ids}
        yield _sse({"type": "status", "message": "Detecting intent"})

        intent = await detect_intent(
            question=question,
            selected_doc_ids=document_ids,
            documents=[
                IntentDocument(document_id=doc_id, document_name=document_names[doc_id])
                for doc_id in document_ids
            ],
        )
        intent_payload = _intent_payload(intent)
        yield _sse({"type": "intent", **intent_payload})

        if intent.intent == IntentType.QUIZ:
            if intent.quiz_scope not in {
                QuizScope.CONTEXT_BASED,
                QuizScope.TOPIC_BASED,
            }:
                yield _sse(
                    {
                        "type": "token",
                        "content": (
                            "Quiz intent detected. This quiz scope is not "
                            "implemented yet."
                        ),
                    }
                )
                yield _sse({"type": "done"})
                return

            try:
                source_message_id = await _ensure_quiz_source_message(
                    chat_id=chat_id,
                    user_id=user_id,
                    question=question,
                    conversation=conversation,
                    request_message_id=body.message_id,
                    config=body.quiz_config,
                )
            except (RuntimeError, ValueError) as exc:
                yield _sse({"type": "error", "message": str(exc)})
                yield _sse({"type": "done"})
                return

            quiz_config = _resolve_quiz_config(intent, body.quiz_config)
            if quiz_config.missing_fields:
                yield _sse(
                    {
                        "type": "quiz_configuration_required",
                        "source_message_id": source_message_id,
                        "missing_fields": quiz_config.missing_fields,
                        "intent": intent_payload,
                    }
                )
                yield _sse({"type": "done"})
                return

            try:
                existing_quiz = await crud.get_generated_quiz_by_source_message(
                    chat_id=chat_id,
                    user_id=user_id,
                    source_message_id=source_message_id,
                )
                if existing_quiz is not None:
                    quiz_payload = GeneratedQuizInDB.model_validate(
                        existing_quiz
                    ).model_dump(mode="json", by_alias=False)
                    await _ensure_quiz_response_message(quiz_payload)
                    yield _sse(
                        {"type": "quiz", "data": _quiz_sse_reference(quiz_payload)}
                    )
                    yield _sse({"type": "done"})
                    return
            except Exception:
                logger.exception("Existing generated quiz recovery failed")
                yield _sse(
                    {
                        "type": "error",
                        "message": "Unable to load the generated quiz.",
                    }
                )
                yield _sse({"type": "done"})
                return

            quiz_doc_ids = [
                doc_id
                for doc_id in (intent.doc_ids or document_ids)
                if doc_id in document_names
            ] or document_ids
            prior_conversation = _prior_quiz_conversation(
                conversation,
                source_message_id,
            )
            status_message = (
                "Resolving quiz context"
                if intent.quiz_scope == QuizScope.CONTEXT_BASED
                else "Generating topic quiz"
            )
            yield _sse({"type": "status", "message": status_message})

            try:
                if intent.quiz_scope == QuizScope.CONTEXT_BASED:
                    quiz = await generate_context_based_quiz(
                        user_id=user_id,
                        chat_id=chat_id,
                        doc_ids=quiz_doc_ids,
                        document_names=document_names,
                        question=question,
                        conversation=prior_conversation,
                        memory_summary=memory.get("summary", ""),
                        number_of_questions=quiz_config.number_of_questions,
                        difficulty=(
                            intent.difficulty.value if intent.difficulty else None
                        ),
                        question_formats=quiz_config.question_formats,
                        mode=quiz_config.mode,
                    )
                else:
                    quiz = await generate_topic_based_quiz(
                        user_id=user_id,
                        chat_id=chat_id,
                        doc_ids=quiz_doc_ids,
                        target=intent.target or "",
                        document_names=document_names,
                        query=question,
                        number_of_questions=quiz_config.number_of_questions,
                        difficulty=(
                            intent.difficulty.value if intent.difficulty else None
                        ),
                        question_formats=quiz_config.question_formats,
                        mode=quiz_config.mode,
                    )
            except ValueError as exc:
                yield _sse({"type": "error", "message": str(exc)})
                yield _sse({"type": "done"})
                return
            except Exception:
                logger.exception("Quiz generation pipeline failed")
                yield _sse(
                    {
                        "type": "error",
                        "message": "Unable to generate the quiz. Please try again.",
                    }
                )
                yield _sse({"type": "done"})
                return

            linked_quiz = quiz.model_copy(
                update={
                    "source_message_id": source_message_id,
                    "response_message_id": str(uuid4()),
                }
            )
            try:
                quiz_payload = await _persist_generated_quiz(linked_quiz)
                await _ensure_quiz_response_message(quiz_payload)
            except Exception:
                logger.exception("Generated quiz persistence failed")
                yield _sse(
                    {
                        "type": "error",
                        "message": "Unable to store the generated quiz.",
                    }
                )
                yield _sse({"type": "done"})
                return

            yield _sse(
                {"type": "quiz", "data": _quiz_sse_reference(quiz_payload)}
            )
            yield _sse({"type": "done"})
            return

        if intent.intent == IntentType.SUMMARIZATION:
            summary_doc_ids = [
                doc_id for doc_id in (intent.doc_ids or document_ids)
                if doc_id in document_names
            ] or document_ids
            summary_request = ChatRequest(
                user_id=user_id,
                chat_id=chat_id,
                message_id=body.message_id,
                question=question,
                document_ids=summary_doc_ids,
                document_names=document_names,
                summary=memory.get("summary", ""),
                recent_messages=[
                    {"role": m.get("role"), "content": m.get("content", "")}
                    for m in conversation[-settings.memory_recent_messages :]
                ],
            )
            answer_parts: list[str] = []
            summary_response: DocMindResponse | None = None
            done_event: dict = {"type": "done"}

            try:
                async for event in stream_level1_pdf_with_outline(
                    target=intent.target,
                    doc_ids=summary_doc_ids,
                    user_id=user_id,
                    document_names=document_names,
                    question=question,
                    deep_summary=body.deep_summary,
                ):
                    event_type = event.get("type")
                    if event_type == "token":
                        answer_parts.append(str(event.get("content") or ""))
                    elif event_type == "final" and isinstance(event.get("data"), dict):
                        summary_response = DocMindResponse(**event["data"])
                    elif event_type == "done":
                        done_event = event
                        continue
                    yield _sse(event)
            except asyncio.CancelledError:
                partial_answer = "".join(answer_parts).strip()
                if partial_answer:
                    try:
                        await asyncio.shield(
                            save_chat_messages(
                                summary_request,
                                partial_answer,
                                None,
                                cancelled=True,
                            )
                        )
                    except Exception:
                        logger.exception(
                            "Failed to persist partial summary after disconnect"
                        )
                raise

            if summary_response is not None:
                try:
                    await asyncio.shield(
                        save_chat_messages(
                            summary_request,
                            summary_response.answer,
                            summary_response,
                        )
                    )
                except Exception:
                    logger.exception("Failed to persist summary conversation")
            yield _sse(done_event)
            return

        request = ChatRequest(
            user_id=user_id,
            chat_id=chat_id,
            message_id=body.message_id,
            question=question,
            document_ids=document_ids,
            document_names=document_names,
            summary=memory.get("summary", ""),
            recent_messages=[
                {"role": m.get("role"), "content": m.get("content", "")}
                for m in conversation[-settings.memory_recent_messages :]
            ],
        )
        async for event in ask_question(request):
            yield _sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Disable proxy buffering (nginx) so tokens flush immediately.
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/{chat_id}/pdfs/{document_db_id}", response_model=ChatResponse)
async def delete_chat_pdf(
    chat_id: str,
    document_db_id: str,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> ChatResponse:
    """Remove a PDF from the chat and delete it from Cloudinary."""
    chat = await crud.get_chat(chat_id=chat_id, user_id=user_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    documents = await crud.get_documents_by_ids(
        document_ids=chat.get("doc_ids", []),
        user_id=user_id,
    )
    target = next((doc for doc in documents if doc["id"] == document_db_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="PDF not found in chat")

    updated, detached_document = await crud.detach_document_from_chat(
        chat_id=chat_id,
        user_id=user_id,
        document_db_id=document_db_id,
    )
    if updated is None or detached_document is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    if not detached_document.get("chat_ids"):
        try:
            delete_pdf(
                detached_document["public_id"],
                resource_type=detached_document.get("resource_type", "raw"),
            )
        except Exception:  # pragma: no cover - best-effort external cleanup
            logger.exception("Cloudinary cleanup failed for document %s", document_db_id)
        try:
            delete_pdf_embeddings(
                user_id=user_id,
                document_id=detached_document["document_id"],
            )
        except Exception:  # pragma: no cover - best-effort external cleanup
            logger.exception("Chunk-vector cleanup failed for document %s", document_db_id)
        try:
            delete_table_vectors(
                user_id=user_id,
                document_id=detached_document["document_id"],
            )
        except Exception:  # pragma: no cover - best-effort external cleanup
            logger.exception("Table-vector cleanup failed for document %s", document_db_id)
        await crud.delete_orphan_document(
            document_db_id=document_db_id,
            user_id=user_id,
        )

    return await _to_chat_response(updated)


@router.get("/{user_id}/chats", response_model=list[ChatResponse])
async def get_all_chats(
    user_id: str,
    authenticated_user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> list[ChatResponse]:
    if user_id != authenticated_user_id:
        raise HTTPException(
            status_code=403,
            detail="Cannot access chats for another user",
        )

    try:
        chats = await crud.get_user_chats(user_id=user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    if chats is None:
        raise HTTPException(status_code=404, detail="User not found")

    return [await _to_chat_response(chat) for chat in chats]
