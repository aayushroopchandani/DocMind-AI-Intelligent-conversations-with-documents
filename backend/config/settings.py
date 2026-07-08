from __future__ import annotations

import os
from dataclasses import dataclass

# Load variables from backend/.env before the Settings defaults are evaluated.
# The dataclass field defaults below read os.getenv at *class definition* time,
# so load_dotenv MUST run first — otherwise the FastAPI app would silently treat
# MongoDB/Cloudinary as "not configured" even when .env is present.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


@dataclass(frozen=True)
class Settings:
    # MongoDB
    mongodb_uri: str = os.getenv("MONGODB_URI", "")
    mongodb_db_name: str = os.getenv("MONGODB_DB_NAME", "docmind")

    # Cloudinary
    cloudinary_cloud_name: str = os.getenv("CLOUDINARY_CLOUD_NAME", "")
    cloudinary_api_key: str = os.getenv("CLOUDINARY_API_KEY", "")
    cloudinary_api_secret: str = os.getenv("CLOUDINARY_API_SECRET", "")

    # Max PDFs allowed per chat (matches the frontend limit).
    max_pdfs_per_chat: int = int(os.getenv("MAX_PDFS_PER_CHAT", "4"))

    # Shared secret between the Next.js server (proxy) and this API. When set,
    # requests must include it in the `X-Internal-Secret` header. Leave empty in
    # local dev to disable the check.
    internal_api_secret: str = os.getenv("INTERNAL_API_SECRET", "")

    # ------------------------------------------------------------------ #
    # RAG / conversation memory tuning
    # ------------------------------------------------------------------ #
    # How many of the latest conversation messages are sent verbatim to the LLM.
    memory_recent_messages: int = int(os.getenv("MEMORY_RECENT_MESSAGES", "6"))
    # Refresh the rolling summary once this many new messages accumulate past
    # the last summarized point.
    memory_summary_every: int = int(os.getenv("MEMORY_SUMMARY_EVERY", "6"))

    # Retrieval sizing: candidates fetched per generated query, then reduced.
    retrieval_candidates_per_doc: int = int(os.getenv("RETRIEVAL_CANDIDATES_PER_DOC", "6"))
    retrieval_final_chunks: int = int(os.getenv("RETRIEVAL_FINAL_CHUNKS", "12"))
    retrieval_max_per_doc: int = int(os.getenv("RETRIEVAL_MAX_PER_DOC", "4"))
    # Approximate token budget for the document context block.
    retrieval_max_context_tokens: int = int(os.getenv("RETRIEVAL_MAX_CONTEXT_TOKENS", "6000"))

    @property
    def mongodb_is_configured(self) -> bool:
        return bool(self.mongodb_uri and self.mongodb_db_name)

    @property
    def cloudinary_is_configured(self) -> bool:
        return bool(
            self.cloudinary_cloud_name
            and self.cloudinary_api_key
            and self.cloudinary_api_secret
        )


settings = Settings()
