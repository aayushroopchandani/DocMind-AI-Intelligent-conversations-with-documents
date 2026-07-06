from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class AnswerStatus(str, Enum):
    COMPLETE = "complete"           # fully answered from context
    PARTIAL = "partial"             # only some parts supported
    CONFLICTING = "conflicting"     # sources disagree
    NOT_FOUND = "not_found"         # context has no relevant info


class Citation(BaseModel):
    document_name: str = Field(..., description="Exact document name from context")
    page_number: int = Field(..., description="Page number from context")
    quote: str = Field(
        ...,
        description="Exact verbatim snippet from the document that supports the claim",
    )


class Claim(BaseModel):
    """A single factual statement in the answer, tied to its supporting citations."""
    statement: str = Field(..., description="The factual statement being made")
    citation_ids: list[int] = Field(
        ...,
        description="Indices into the top-level citations list that support this claim",
    )


class DocMindResponse(BaseModel):
    answer: str = Field(
    ...,
    description=(
        "The user-facing answer in Markdown. Write it as a direct reply to the user's "
        "question, in your own words, using the document context as evidence rather than "
        "as text to reproduce. Cite factual claims inline as [Document Name, Page X]. "
        "Format for readability: use paragraphs, bullets, or headings as needed, and "
        "include code blocks with concrete examples when the user asks about code, APIs, "
        "commands, config, or anything else best shown as code — derive the examples from "
        "the document context. Put exact source text in Citation.quote — this field is "
        "for the explanation."
    ),
    )
    status: AnswerStatus
    citations: list[Citation] = Field(
        default_factory=list,
        description="All unique sources referenced in the answer. Note: unique only",
    )
    claims: Optional[list[Claim]] = Field(
        default=None,
        description=(
            "Optional structured breakdown of the answer into atomic factual statements, "
            "each linked to its supporting citations. Useful for per-claim highlighting or "
            "grounding verification in the UI. Omit for short or conversational answers "
        ),
    )
    missing_information: Optional[str] = Field(
        None,
        description="Set when status=partial. What the context did NOT cover.",
    )
    conflict_notes: Optional[str] = Field(
        None,
        description="Set when status=conflicting. Summary of what differs between sources.",
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Model self-rated confidence based on how directly context supports the answer",
    )
    follow_up_questions: list[str] = Field(
        default_factory=list,
        description="0–3 useful next questions the user could ask about this document",
    )