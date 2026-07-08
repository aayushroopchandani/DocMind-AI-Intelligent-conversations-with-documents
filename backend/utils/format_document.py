"""
Context building for multi-document RAG.

Takes the raw (possibly overlapping) chunks returned by the MultiQueryRetriever
and turns them into a compact, citation-ready context block:

    retrieval order preserved -> dedupe -> per-document balancing
    -> token budget -> stable citation IDs ([C1], [C2], ...)
"""

from __future__ import annotations

import hashlib
import re

from utils.pydantic_schemas import Citation


def _approx_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars per token) — good enough for budgeting."""
    return max(1, len(text) // 4)


def _chunk_fingerprint(document) -> str:
    """Stable identity for a chunk so duplicates across generated queries collapse."""
    doc_id = document.metadata.get("doc_id", "")
    page = document.metadata.get("page", "")
    normalized = re.sub(r"\s+", " ", document.page_content).strip().lower()
    return hashlib.sha1(f"{doc_id}|{page}|{normalized}".encode()).hexdigest()


def deduplicate_documents(documents: list) -> list:
    """Drop identical chunks returned by different generated queries (order kept)."""
    seen: set[str] = set()
    unique = []
    for document in documents:
        fingerprint = _chunk_fingerprint(document)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(document)
    return unique


def balance_documents(
    documents: list,
    max_total: int = 12,
    max_per_doc: int = 4,
) -> list:
    """
    Reduce candidates to the final context set while respecting retrieval rank.

    - Never keeps more than `max_per_doc` chunks from one PDF, so a single
      document cannot consume the whole context window.
    - Does NOT force chunks from irrelevant documents: if a PDF returned
      nothing useful it simply won't appear.
    """
    per_doc_count: dict[str, int] = {}
    selected = []
    for document in documents:
        if len(selected) >= max_total:
            break
        doc_id = document.metadata.get("doc_id", "unknown")
        if per_doc_count.get(doc_id, 0) >= max_per_doc:
            continue
        per_doc_count[doc_id] = per_doc_count.get(doc_id, 0) + 1
        selected.append(document)
    return selected


def format_documents_with_citations(
    documents: list,
    document_names: dict[str, str] | None = None,
    max_context_tokens: int = 6000,
    excerpt_chars: int = 220,
) -> tuple[str, list[Citation]]:
    """
    Format the final chunk set into the LLM context block and build the
    matching citation objects the frontend will receive.

    Each chunk becomes:

        [C1]
        Document: Employee Handbook.pdf
        Document ID: doc_101
        Page: 12

        The employee is entitled to...
    """
    document_names = document_names or {}
    blocks: list[str] = []
    citations: list[Citation] = []
    used_tokens = 0

    for index, document in enumerate(documents, start=1):
        metadata = document.metadata or {}
        doc_id = str(metadata.get("doc_id", "unknown"))
        name = document_names.get(doc_id) or metadata.get("source") or "Unknown document"
        raw_page = metadata.get("page")
        # PyMuPDF pages are 0-based; expose 1-based pages everywhere else.
        page = int(raw_page) + 1 if isinstance(raw_page, (int, float)) else None
        content = document.page_content.strip()

        citation_id = f"C{index}"
        header_lines = [f"[{citation_id}]", f"Document: {name}", f"Document ID: {doc_id}"]
        if page is not None:
            header_lines.append(f"Page: {page}")
        block = "\n".join(header_lines) + f"\n\n{content}"

        block_tokens = _approx_tokens(block)
        if used_tokens + block_tokens > max_context_tokens and blocks:
            break
        used_tokens += block_tokens

        blocks.append(block)
        citations.append(
            Citation(
                citation_id=citation_id,
                document_id=doc_id,
                document_name=name,
                page_number=page,
                chunk_id=_chunk_fingerprint(document)[:12],
                excerpt=(content[:excerpt_chars] + "…") if len(content) > excerpt_chars else content,
            )
        )

    return "\n\n---\n\n".join(blocks), citations


def format_documents(documents: list) -> str:
    """Legacy plain formatter (kept for backwards compatibility)."""
    context, _ = format_documents_with_citations(documents)
    return context
