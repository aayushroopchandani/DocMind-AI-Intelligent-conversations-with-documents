"""Chat + PDF endpoints.

Uploads flow: browser -> Next.js proxy (verifies Clerk) -> here. We push the
file to Cloudinary (namespaced by user + chat) and store the returned asset
metadata on the chat document. LangChain/Qdrant ingestion is intentionally
left untouched for now.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from apis.deps import current_user_id, verify_internal_secret
from config.settings import settings
from db import crud
from db.models.chat import CloudinaryPdf
from services.cloudinary_setup import delete_pdf, upload_pdf
from scripts.chat_with_pdf import ask_question
from scripts.ingest import ingest_pdf
from utils.pydantic_schemas import ChatRequest, IngestData, StreamAskRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])


class ChatResponse(BaseModel):
    id: str
    user_id: str
    pdf: list[CloudinaryPdf] = []


def _to_chat_response(chat: dict) -> ChatResponse:
    return ChatResponse(
        id=chat["id"],
        user_id=chat["user_id"],
        pdf=[CloudinaryPdf(**p) for p in chat.get("pdf", [])],
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
    return _to_chat_response(chat)


@router.get("/{chat_id}", response_model=ChatResponse)
async def get_chat(
    chat_id: str,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> ChatResponse:
    chat = await crud.get_chat(chat_id=chat_id, user_id=user_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return _to_chat_response(chat)


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

    existing = chat.get("pdf", [])
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
        print(file.filename) # imp
        filename = file.filename or "document.pdf"
        is_pdf = (file.content_type == "application/pdf") or filename.lower().endswith(
            ".pdf"
        )
        if not is_pdf:
            raise HTTPException(
                status_code=400, detail=f"'{filename}' is not a PDF. Only PDFs are allowed."
            )

        data = await file.read()
        try:
            asset = upload_pdf(
                data, user_id=user_id, chat_id=chat_id, filename=filename
            )

            # do the ingestion of pdf to qdrant here 
            data = IngestData(
                secure_url=asset["secure_url"],
                filename= asset["filename"],
                doc_id=asset["public_id"],
                user_id=user_id
            )

            ingest_pdf(data)

        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:  # pragma: no cover - surface upstream errors
            logger.exception("Cloudinary upload failed for %s", filename)
            raise HTTPException(status_code=502, detail=f"Upload failed: {exc}")

        pdf = CloudinaryPdf(
            public_id=asset["public_id"],
            private_id=asset["asset_id"],
            secure_url=asset["secure_url"],
            resource_type=asset["resource_type"],
            filename=asset["filename"],
            bytes=asset.get("bytes"),
            pages=asset.get("pages"),
        )
        try:
            updated = await crud.add_pdf_to_chat(
                chat_id=chat_id, user_id=user_id, pdf=pdf.model_dump()
            )

        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    return _to_chat_response(updated)


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

    attached = {p["public_id"]: p.get("filename") or "document.pdf" for p in chat.get("pdf", [])}
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
    request = ChatRequest(
        user_id=user_id,
        chat_id=chat_id,
        question=body.question.strip(),
        document_ids=document_ids,
        document_names={doc_id: attached[doc_id] for doc_id in document_ids},
        summary=memory.get("summary", ""),
        recent_messages=[
            {"role": m.get("role"), "content": m.get("content", "")}
            for m in conversation[-settings.memory_recent_messages :]
        ],
    )

    async def event_stream():
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


@router.delete("/{chat_id}/pdfs/{public_id:path}", response_model=ChatResponse)
async def delete_chat_pdf(
    chat_id: str,
    public_id: str,
    user_id: str = Depends(current_user_id),
    _: None = Depends(verify_internal_secret),
) -> ChatResponse:
    """Remove a PDF from the chat and delete it from Cloudinary."""
    chat = await crud.get_chat(chat_id=chat_id, user_id=user_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    target = next(
        (p for p in chat.get("pdf", []) if p.get("public_id") == public_id), None
    )
    if target is None:
        raise HTTPException(status_code=404, detail="PDF not found in chat")

    try:
        delete_pdf(public_id, resource_type=target.get("resource_type", "image"))
    except Exception:  # pragma: no cover - best-effort cleanup
        logger.exception("Cloudinary delete failed for %s", public_id)

    updated = await crud.remove_pdf_from_chat(
        chat_id=chat_id, user_id=user_id, public_id=public_id
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return _to_chat_response(updated)
