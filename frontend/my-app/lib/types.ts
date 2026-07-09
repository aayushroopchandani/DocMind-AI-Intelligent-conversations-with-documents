/**
 * Shared domain types for the DocMind chat experience.
 *
 * These types are intentionally decoupled from any backend implementation so
 * the frontend can be wired to a real API later without structural changes.
 */

export type ChatRole = "user" | "assistant";

/** A single citation pointing back into the source document. */
export interface Citation {
  /** Stable marker used inline in the answer, e.g. "C1". */
  citationId: string;
  /** SHA-256 document id used by retrieval and citations. */
  documentId: string;
  documentName: string;
  pageNumber?: number | null;
  /** Short snippet from the cited chunk. */
  excerpt?: string | null;
}

/** How one PDF contributed to an answer. */
export interface DocumentContribution {
  documentId: string;
  documentName: string;
  contribution: string;
  relevantPages: number[];
  citationIds: string[];
}

/** Structured enrichment delivered by the `final` SSE event. */
export interface StructuredAnswer {
  answerFound: boolean;
  status: "complete" | "partial" | "conflicting" | "not_found";
  confidenceScore?: number | null;
  followUpQuestions: string[];
  contributions: DocumentContribution[];
}

/** Lifecycle of an assistant message while its answer streams in. */
export type MessageStatus = "streaming" | "complete" | "error" | "cancelled";

/** A message rendered in the chat transcript. */
export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  citations?: Citation[];
  structured?: StructuredAnswer;
  /** Present on assistant messages produced via streaming. */
  status?: MessageStatus;
  /** Pipeline progress text shown before the first token arrives. */
  statusText?: string;
  /** Client timestamp (ms). Used only for ordering/animation, never persisted. */
  createdAt: number;
}

/* ------------------------------------------------------------------ */
/* Raw SSE payloads from the FastAPI backend (snake_case)              */
/* ------------------------------------------------------------------ */

export interface BackendCitation {
  citation_id: string;
  document_id: string;
  document_name: string;
  page_number?: number | null;
  chunk_id?: string | null;
  excerpt?: string | null;
}

export interface BackendContribution {
  document_id: string;
  document_name: string;
  contribution: string;
  relevant_pages: number[];
  citation_ids: string[];
}

export interface BackendFinalData {
  answer: string;
  answer_found: boolean;
  status: "complete" | "partial" | "conflicting" | "not_found";
  document_contributions: BackendContribution[];
  citations: BackendCitation[];
  confidence_score?: number | null;
  follow_up_questions: string[];
}

export type StreamEvent =
  | { type: "status"; message: string }
  | { type: "token"; content: string }
  | { type: "citations"; citations: BackendCitation[] }
  | { type: "final"; data: BackendFinalData }
  | { type: "error"; message: string }
  | { type: "done" };

/** Upload/processing lifecycle for a document tab. */
export type PdfDocStatus = "uploading" | "ready" | "error";

/**
 * A PDF tracked by the workspace. The blob `url` is used by react-pdf for
 * instant local rendering; Cloudinary fields are populated once the upload
 * to the backend completes.
 */
export interface PdfDoc {
  /** Local, client-generated id (stable across the session). */
  id: string;
  name: string;
  sizeBytes: number;
  /** Object URL from URL.createObjectURL — revoked when the doc is removed. */
  url: string;
  status: PdfDocStatus;
  error?: string;
  /** Page count reported by react-pdf once the document loads. */
  numPages: number;
  /** Per-document viewer state, preserved when switching tabs. */
  lastPage: number;
  scale: number;
  fitWidth: boolean;
  /** Backend identifiers, set after a successful upload. */
  documentDbId?: string;
  documentId?: string;
  publicId?: string;
  secureUrl?: string;
  cloudinaryPages?: number;
}

/** Shared PDF document populated by the backend. */
export interface PdfDocumentRecord {
  _id: string;
  document_id: string;
  user_id: string;
  chat_ids: string[];
  ingestion_status: "ready" | "not_ready";
  public_id: string;
  private_id: string;
  secure_url: string;
  resource_type: string;
  filename: string;
  bytes?: number | null;
  pages?: number | null;
}

/** Chat document shape returned by the backend API. */
export interface ChatApiResponse {
  id: string;
  user_id: string;
  doc_ids: string[];
  documents: PdfDocumentRecord[];
}
