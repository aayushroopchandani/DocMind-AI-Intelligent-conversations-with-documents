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

    # Shared secret between the Next.js server (proxy) and this API. The API
    # fails closed when it is missing, including in local development.
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

    # ------------------------------------------------------------------ #
    # Durable data-analysis runtime (Phase 8)
    # ------------------------------------------------------------------ #
    # Inline workbook snapshots are intentionally bounded. Larger workbook
    # contexts must be synchronized as immutable artifacts instead of being
    # copied into an analysis-run document.
    analysis_max_inline_cells: int = int(
        os.getenv("ANALYSIS_MAX_INLINE_CELLS", "25000")
    )
    analysis_max_inline_bytes: int = int(
        os.getenv("ANALYSIS_MAX_INLINE_BYTES", str(5 * 1024 * 1024))
    )
    # Enforced by an ASGI receive wrapper before FastAPI/Pydantic materialize
    # the four workbook matrices. Keep a small allowance over the canonical
    # snapshot cap for the request envelope and JSON field names.
    analysis_max_run_request_bytes: int = int(
        os.getenv("ANALYSIS_MAX_RUN_REQUEST_BYTES", str(6 * 1024 * 1024))
    )
    analysis_max_uploaded_snapshot_cells: int = int(
        os.getenv("ANALYSIS_MAX_UPLOADED_SNAPSHOT_CELLS", "250000")
    )
    analysis_max_uploaded_snapshot_bytes: int = int(
        os.getenv(
            "ANALYSIS_MAX_UPLOADED_SNAPSHOT_BYTES",
            str(25 * 1024 * 1024),
        )
    )
    analysis_max_dataset_columns: int = int(
        os.getenv("ANALYSIS_MAX_DATASET_COLUMNS", "500")
    )
    analysis_max_datasets_per_workbook: int = int(
        os.getenv("ANALYSIS_MAX_DATASETS_PER_WORKBOOK", "50")
    )

    # Artifact uploads are provider-neutral at the domain boundary. These
    # limits apply before a payload reaches Cloudinary and also guard XLSX
    # decompression.
    analysis_max_artifact_bytes: int = int(
        os.getenv("ANALYSIS_MAX_ARTIFACT_BYTES", str(50 * 1024 * 1024))
    )
    # Multipart framing and bounded metadata sit outside the file bytes. This
    # transport cap prevents Starlette from parsing/spooling an unbounded body
    # while retaining enough headroom for a maximum-sized valid artifact.
    analysis_max_artifact_request_bytes: int = int(
        os.getenv(
            "ANALYSIS_MAX_ARTIFACT_REQUEST_BYTES",
            str(51 * 1024 * 1024),
        )
    )
    analysis_max_xlsx_uncompressed_bytes: int = int(
        os.getenv(
            "ANALYSIS_MAX_XLSX_UNCOMPRESSED_BYTES",
            str(100 * 1024 * 1024),
        )
    )
    analysis_max_xlsx_entries: int = int(
        os.getenv("ANALYSIS_MAX_XLSX_ENTRIES", "10000")
    )
    analysis_max_xlsx_compression_ratio: float = float(
        os.getenv("ANALYSIS_MAX_XLSX_COMPRESSION_RATIO", "100")
    )

    # Spreadsheet import/export converts inside the request, so its caps are
    # tighter than durable artifact storage: the ceiling is what a browser
    # grid can hold and a request can convert without blocking a worker.
    analysis_max_spreadsheet_bytes: int = int(
        os.getenv("ANALYSIS_MAX_SPREADSHEET_BYTES", str(10 * 1024 * 1024))
    )
    analysis_max_spreadsheet_request_bytes: int = int(
        os.getenv(
            "ANALYSIS_MAX_SPREADSHEET_REQUEST_BYTES",
            str(12 * 1024 * 1024),
        )
    )
    analysis_spreadsheet_max_cells: int = int(
        os.getenv("ANALYSIS_SPREADSHEET_MAX_CELLS", "400000")
    )
    analysis_spreadsheet_max_sheets: int = int(
        os.getenv("ANALYSIS_SPREADSHEET_MAX_SHEETS", "64")
    )

    # SSE is backed by durable MongoDB events. Polling is deliberately modest
    # until the deployment is ready to use MongoDB change streams.
    analysis_sse_poll_seconds: float = float(
        os.getenv("ANALYSIS_SSE_POLL_SECONDS", "0.75")
    )
    analysis_sse_heartbeat_seconds: float = float(
        os.getenv("ANALYSIS_SSE_HEARTBEAT_SECONDS", "15")
    )
    analysis_sse_batch_size: int = int(
        os.getenv("ANALYSIS_SSE_BATCH_SIZE", "100")
    )
    analysis_sse_max_connections: int = int(
        os.getenv("ANALYSIS_SSE_MAX_CONNECTIONS", "200")
    )
    analysis_sse_max_connections_per_user: int = int(
        os.getenv("ANALYSIS_SSE_MAX_CONNECTIONS_PER_USER", "8")
    )
    analysis_sse_max_connections_per_run: int = int(
        os.getenv("ANALYSIS_SSE_MAX_CONNECTIONS_PER_RUN", "2")
    )

    # Database-backed execution leases prevent multiple API workers from
    # processing the same durable run. A worker that disappears can be safely
    # replaced once its lease expires.
    analysis_run_deadline_seconds: int = int(
        os.getenv("ANALYSIS_RUN_DEADLINE_SECONDS", str(2 * 60 * 60))
    )
    analysis_input_initialization_timeout_seconds: int = int(
        os.getenv(
            "ANALYSIS_INPUT_INITIALIZATION_TIMEOUT_SECONDS",
            str(15 * 60),
        )
    )
    analysis_worker_concurrency: int = int(
        os.getenv("ANALYSIS_WORKER_CONCURRENCY", "2")
    )
    analysis_worker_poll_seconds: float = float(
        os.getenv("ANALYSIS_WORKER_POLL_SECONDS", "0.75")
    )
    analysis_worker_lease_seconds: int = int(
        os.getenv("ANALYSIS_WORKER_LEASE_SECONDS", "60")
    )
    analysis_worker_renew_seconds: int = int(
        os.getenv("ANALYSIS_WORKER_RENEW_SECONDS", "20")
    )

    # Typed planning and deterministic validation limits. Plans contain only
    # schemas/statistics/references, never full source rows.
    analysis_max_planning_context_bytes: int = int(
        os.getenv("ANALYSIS_MAX_PLANNING_CONTEXT_BYTES", str(2 * 1024 * 1024))
    )
    analysis_max_plan_bytes: int = int(
        os.getenv("ANALYSIS_MAX_PLAN_BYTES", str(1024 * 1024))
    )
    analysis_max_plan_steps: int = int(
        os.getenv("ANALYSIS_MAX_PLAN_STEPS", "32")
    )
    analysis_max_plan_rows_scanned: int = int(
        os.getenv("ANALYSIS_MAX_PLAN_ROWS_SCANNED", "2000000")
    )
    analysis_max_plan_cells_written: int = int(
        os.getenv("ANALYSIS_MAX_PLAN_CELLS_WRITTEN", "250000")
    )
    analysis_max_plan_joins: int = int(
        os.getenv("ANALYSIS_MAX_PLAN_JOINS", "4")
    )
    analysis_max_python_memory_mb: int = int(
        os.getenv("ANALYSIS_MAX_PYTHON_MEMORY_MB", "512")
    )
    analysis_max_python_seconds: float = float(
        os.getenv("ANALYSIS_MAX_PYTHON_SECONDS", "120")
    )
    analysis_max_plan_cost_usd: float = float(
        os.getenv("ANALYSIS_MAX_PLAN_COST_USD", "1.0")
    )
    analysis_max_generated_rows: int = int(
        os.getenv("ANALYSIS_MAX_GENERATED_ROWS", "100000")
    )
    analysis_max_chart_cardinality: int = int(
        os.getenv("ANALYSIS_MAX_CHART_CARDINALITY", "500")
    )
    analysis_plan_approval_python_seconds: float = float(
        os.getenv("ANALYSIS_PLAN_APPROVAL_PYTHON_SECONDS", "15")
    )
    analysis_plan_approval_cost_usd: float = float(
        os.getenv("ANALYSIS_PLAN_APPROVAL_COST_USD", "0.10")
    )
    analysis_plan_approval_generated_rows: int = int(
        os.getenv("ANALYSIS_PLAN_APPROVAL_GENERATED_ROWS", "25000")
    )

    def __post_init__(self) -> None:
        phase8_positive = {
            "ANALYSIS_MAX_INLINE_CELLS": self.analysis_max_inline_cells,
            "ANALYSIS_MAX_INLINE_BYTES": self.analysis_max_inline_bytes,
            "ANALYSIS_MAX_RUN_REQUEST_BYTES": (
                self.analysis_max_run_request_bytes
            ),
            "ANALYSIS_MAX_UPLOADED_SNAPSHOT_CELLS": (
                self.analysis_max_uploaded_snapshot_cells
            ),
            "ANALYSIS_MAX_UPLOADED_SNAPSHOT_BYTES": (
                self.analysis_max_uploaded_snapshot_bytes
            ),
            "ANALYSIS_MAX_DATASET_COLUMNS": self.analysis_max_dataset_columns,
            "ANALYSIS_MAX_DATASETS_PER_WORKBOOK": (
                self.analysis_max_datasets_per_workbook
            ),
            "ANALYSIS_MAX_ARTIFACT_BYTES": self.analysis_max_artifact_bytes,
            "ANALYSIS_MAX_ARTIFACT_REQUEST_BYTES": (
                self.analysis_max_artifact_request_bytes
            ),
            "ANALYSIS_MAX_XLSX_UNCOMPRESSED_BYTES": (
                self.analysis_max_xlsx_uncompressed_bytes
            ),
            "ANALYSIS_MAX_XLSX_ENTRIES": self.analysis_max_xlsx_entries,
            "ANALYSIS_MAX_SPREADSHEET_BYTES": (
                self.analysis_max_spreadsheet_bytes
            ),
            "ANALYSIS_MAX_SPREADSHEET_REQUEST_BYTES": (
                self.analysis_max_spreadsheet_request_bytes
            ),
            "ANALYSIS_SPREADSHEET_MAX_CELLS": (
                self.analysis_spreadsheet_max_cells
            ),
            "ANALYSIS_SPREADSHEET_MAX_SHEETS": (
                self.analysis_spreadsheet_max_sheets
            ),
            "ANALYSIS_SSE_POLL_SECONDS": self.analysis_sse_poll_seconds,
            "ANALYSIS_SSE_HEARTBEAT_SECONDS": (
                self.analysis_sse_heartbeat_seconds
            ),
            "ANALYSIS_SSE_BATCH_SIZE": self.analysis_sse_batch_size,
            "ANALYSIS_SSE_MAX_CONNECTIONS": (
                self.analysis_sse_max_connections
            ),
            "ANALYSIS_SSE_MAX_CONNECTIONS_PER_USER": (
                self.analysis_sse_max_connections_per_user
            ),
            "ANALYSIS_SSE_MAX_CONNECTIONS_PER_RUN": (
                self.analysis_sse_max_connections_per_run
            ),
            "ANALYSIS_WORKER_CONCURRENCY": self.analysis_worker_concurrency,
            "ANALYSIS_RUN_DEADLINE_SECONDS": (
                self.analysis_run_deadline_seconds
            ),
            "ANALYSIS_INPUT_INITIALIZATION_TIMEOUT_SECONDS": (
                self.analysis_input_initialization_timeout_seconds
            ),
            "ANALYSIS_WORKER_POLL_SECONDS": self.analysis_worker_poll_seconds,
            "ANALYSIS_WORKER_LEASE_SECONDS": self.analysis_worker_lease_seconds,
            "ANALYSIS_WORKER_RENEW_SECONDS": self.analysis_worker_renew_seconds,
            "ANALYSIS_MAX_PLAN_BYTES": self.analysis_max_plan_bytes,
            "ANALYSIS_MAX_PLANNING_CONTEXT_BYTES": (
                self.analysis_max_planning_context_bytes
            ),
            "ANALYSIS_MAX_PLAN_STEPS": self.analysis_max_plan_steps,
            "ANALYSIS_MAX_PLAN_ROWS_SCANNED": (
                self.analysis_max_plan_rows_scanned
            ),
            "ANALYSIS_MAX_PLAN_CELLS_WRITTEN": (
                self.analysis_max_plan_cells_written
            ),
            "ANALYSIS_MAX_PLAN_JOINS": self.analysis_max_plan_joins,
            "ANALYSIS_MAX_PYTHON_MEMORY_MB": (
                self.analysis_max_python_memory_mb
            ),
            "ANALYSIS_MAX_PYTHON_SECONDS": self.analysis_max_python_seconds,
            "ANALYSIS_MAX_GENERATED_ROWS": self.analysis_max_generated_rows,
            "ANALYSIS_MAX_CHART_CARDINALITY": (
                self.analysis_max_chart_cardinality
            ),
            "ANALYSIS_PLAN_APPROVAL_GENERATED_ROWS": (
                self.analysis_plan_approval_generated_rows
            ),
        }
        invalid = [name for name, value in phase8_positive.items() if value <= 0]
        if invalid:
            raise ValueError(
                "Phase 8 settings must be positive: " + ", ".join(invalid)
            )
        if self.analysis_max_run_request_bytes <= self.analysis_max_inline_bytes:
            raise ValueError(
                "ANALYSIS_MAX_RUN_REQUEST_BYTES must exceed "
                "ANALYSIS_MAX_INLINE_BYTES"
            )
        if (
            self.analysis_max_artifact_request_bytes
            <= self.analysis_max_artifact_bytes
        ):
            raise ValueError(
                "ANALYSIS_MAX_ARTIFACT_REQUEST_BYTES must exceed "
                "ANALYSIS_MAX_ARTIFACT_BYTES"
            )
        if not 1 <= self.analysis_max_dataset_columns <= 500:
            raise ValueError(
                "ANALYSIS_MAX_DATASET_COLUMNS must be between 1 and 500"
            )
        if self.analysis_sse_heartbeat_seconds < self.analysis_sse_poll_seconds:
            raise ValueError(
                "ANALYSIS_SSE_HEARTBEAT_SECONDS cannot be shorter than polling"
            )
        if not 1 <= self.analysis_sse_batch_size <= 500:
            raise ValueError("ANALYSIS_SSE_BATCH_SIZE must be between 1 and 500")
        if not (
            self.analysis_sse_max_connections_per_run
            <= self.analysis_sse_max_connections_per_user
            <= self.analysis_sse_max_connections
        ):
            raise ValueError(
                "SSE connection limits must satisfy per-run <= per-user "
                "<= total"
            )
        if self.analysis_worker_lease_seconds < 3:
            raise ValueError("ANALYSIS_WORKER_LEASE_SECONDS must be at least 3")
        if not 60 <= self.analysis_run_deadline_seconds <= 7 * 24 * 60 * 60:
            raise ValueError(
                "ANALYSIS_RUN_DEADLINE_SECONDS must be between 60 and 604800"
            )
        if not (
            30
            <= self.analysis_input_initialization_timeout_seconds
            <= self.analysis_run_deadline_seconds
        ):
            raise ValueError(
                "ANALYSIS_INPUT_INITIALIZATION_TIMEOUT_SECONDS must be "
                "between 30 and ANALYSIS_RUN_DEADLINE_SECONDS"
            )
        if not (
            1
            <= self.analysis_worker_renew_seconds
            < self.analysis_worker_lease_seconds
        ):
            raise ValueError(
                "ANALYSIS_WORKER_RENEW_SECONDS must be positive and shorter "
                "than the lease"
            )
        if self.analysis_max_xlsx_compression_ratio <= 1:
            raise ValueError(
                "ANALYSIS_MAX_XLSX_COMPRESSION_RATIO must exceed 1"
            )
        if not 1 <= self.analysis_max_plan_steps <= 64:
            raise ValueError("ANALYSIS_MAX_PLAN_STEPS must be between 1 and 64")
        if not 1 <= self.analysis_max_plan_joins <= 16:
            raise ValueError("ANALYSIS_MAX_PLAN_JOINS must be between 1 and 16")
        if self.analysis_max_plan_cost_usd < 0:
            raise ValueError("ANALYSIS_MAX_PLAN_COST_USD cannot be negative")
        if self.analysis_plan_approval_python_seconds < 0:
            raise ValueError(
                "ANALYSIS_PLAN_APPROVAL_PYTHON_SECONDS cannot be negative"
            )
        if self.analysis_plan_approval_cost_usd < 0:
            raise ValueError(
                "ANALYSIS_PLAN_APPROVAL_COST_USD cannot be negative"
            )
        if (
            self.analysis_plan_approval_python_seconds
            > self.analysis_max_python_seconds
        ):
            raise ValueError(
                "Python approval threshold cannot exceed its hard limit"
            )
        if self.analysis_plan_approval_cost_usd > self.analysis_max_plan_cost_usd:
            raise ValueError("cost approval threshold cannot exceed its hard limit")
        if (
            self.analysis_plan_approval_generated_rows
            > self.analysis_max_generated_rows
        ):
            raise ValueError(
                "generated-row approval threshold cannot exceed its hard limit"
            )

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
