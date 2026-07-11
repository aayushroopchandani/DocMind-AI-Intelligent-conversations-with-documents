"""Chat + PDF endpoints.

Uploads flow: browser -> Next.js proxy (verifies Clerk) -> here. We push the
file to Cloudinary (namespaced by user + chat) and store the returned asset
metadata on the chat document. LangChain/Qdrant ingestion is intentionally
left untouched for now.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from apis.deps import current_user_id, verify_internal_secret
from config.settings import settings
from db import crud
from db.models.chat import ChatMemory, ConversationMessage
from db.models.document import PdfDocument
from services.cloudinary_setup import delete_pdf, upload_pdf
from scripts.chat_with_pdf import ask_question
from scripts.intention_pipelines.summarization_pipeline.level1_pdf_with_outline import (
    stream_level1_pdf_with_outline,
)
from scripts.intent_detection import IntentDocument, IntentType, detect_intent
from scripts.ingest import delete_pdf_embeddings, ingest_pdf
from utils.pydantic_schemas import ChatRequest, IngestData, StreamAskRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])


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
                    "nodes": {"nodes":nodes,"ingestion_status":"not_ready"},
                },
            )
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
        yield _sse(
            {
                "type": "intent",
                "intent": intent.intent.value,
                "doc_ids": intent.doc_ids,
                "target": intent.target,
                "quiz_scope": intent.quiz_scope.value if intent.quiz_scope else None,
                "question_formats": [
                    question_format.value
                    for question_format in intent.question_formats
                ],
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
        )

        if intent.intent == IntentType.QUIZ:
            yield _sse({"type": "token", "content": "Quiz intent detected"})
            yield _sse({"type": "done"})
            return

        if intent.intent == IntentType.SUMMARIZATION:
            summary_doc_ids = [
                doc_id for doc_id in (intent.doc_ids or document_ids)
                if doc_id in document_names
            ] or document_ids

            async for event in stream_level1_pdf_with_outline(
                target=intent.target,
                doc_ids=summary_doc_ids,
                user_id=user_id,
                document_names=document_names,
                question=question,
            ):
                yield _sse(event)
            return

        request = ChatRequest(
            user_id=user_id,
            chat_id=chat_id,
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
            logger.exception("Qdrant cleanup failed for document %s", document_db_id)
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
