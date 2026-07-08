from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class AnswerStatus(str, Enum):
    COMPLETE = "complete"           # fully answered from context
    PARTIAL = "partial"             # only some parts supported
    CONFLICTING = "conflicting"     # sources disagree
    NOT_FOUND = "not_found"         # context has no relevant info


class Citation(BaseModel):
    """One retrieved chunk that the answer actually cited via its [Cn] marker."""

    citation_id: str = Field(..., description="Stable marker used in the answer, e.g. 'C1'")
    document_id: str = Field(..., description="Backend document id (Cloudinary public_id / Qdrant doc_id)")
    document_name: str = Field(..., description="Original PDF filename")
    page_number: Optional[int] = Field(default=None, description="1-based page number in the PDF")
    chunk_id: Optional[str] = Field(default=None, description="Internal chunk identifier")
    excerpt: Optional[str] = Field(default=None, description="Short snippet from the cited chunk")


class DocumentContribution(BaseModel):
    """How a single PDF contributed to the final answer."""

    document_id: str
    document_name: str
    contribution: str = Field(default="", description="One-line summary of what this document provided")
    relevant_pages: list[int] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)


class DocMindResponse(BaseModel):
    """Final structured payload sent to the frontend after streaming finishes."""

    answer: str = Field(..., description="User-facing Markdown answer (already streamed token-by-token)")
    answer_found: bool = Field(..., description="False when the selected PDFs did not contain the answer")
    status: AnswerStatus = Field(default=AnswerStatus.COMPLETE)
    document_contributions: list[DocumentContribution] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    confidence_score: Optional[float] = Field(default=None, ge=0, le=1)
    follow_up_questions: list[str] = Field(default_factory=list)


class ResponseMetadata(BaseModel):
    """
    Lightweight structured-output schema for the post-stream metadata call.
    Only fields that cannot be reliably derived from retrieval metadata.
    """

    answer_found: bool = Field(
        ..., description="True only if the answer is actually supported by the document context"
    )
    confidence_score: float = Field(
        ..., ge=0, le=1,
        description="How directly the cited context supports the answer",
    )
    status: AnswerStatus = Field(
        default=AnswerStatus.COMPLETE,
        description="complete | partial | conflicting | not_found",
    )
    follow_up_questions: list[str] = Field(
        default_factory=list,
        description="0-3 short, useful next questions grounded in the same documents",
    )
    document_contributions: list[DocumentContribution] = Field(
        default_factory=list,
        description=(
            "One entry per document that actually contributed. Copy document_id, "
            "document_name, relevant_pages and citation_ids exactly from the provided "
            "citation list; write only the one-line `contribution` yourself."
        ),
    )


class IngestData(BaseModel):
    secure_url: str = Field(..., description="Secure URL of the uploaded PDF")
    filename: str = Field(..., description="Filename of the uploaded PDF")
    doc_id: str = Field(..., description="Document ID")
    user_id: str = Field(..., description="User ID")


class ChatRequest(BaseModel):
    """Internal request passed into the RAG pipeline."""

    user_id: str = Field(..., description="User ID")
    chat_id: str = Field(..., description="Chat ID")
    question: str = Field(..., description="Question")
    document_ids: list[str] = Field(..., description="Document IDs to search")
    document_names: dict[str, str] = Field(
        default_factory=dict, description="doc_id -> original filename"
    )
    summary: str = Field(default="", description="Rolling summary of older conversation")
    recent_messages: list[dict] = Field(
        default_factory=list, description="Last few conversation turns [{role, content}]"
    )


class StreamAskRequest(BaseModel):
    """Body of POST /chats/{chat_id}/stream (user identity comes from headers)."""

    question: str = Field(..., min_length=1, max_length=4000)
    document_ids: Optional[list[str]] = Field(
        default=None,
        description="Subset of the chat's PDFs to search. Defaults to all attached PDFs.",
    )
