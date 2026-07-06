/**
 * Shared domain types for the DocMind chat experience.
 *
 * These types are intentionally decoupled from any backend implementation so
 * the frontend can be wired to a real API later without structural changes.
 */

export type ChatRole = "user" | "assistant";

/** A single citation pointing back into the source document. */
export interface Citation {
  documentName: string;
  pageNumber: number;
  preview?: string;
}

/** A message rendered in the chat transcript. */
export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  citations?: Citation[];
  /** Client timestamp (ms). Used only for ordering/animation, never persisted. */
  createdAt: number;
}

/** Shape returned by the (currently mocked) assistant. */
export interface ChatResponse {
  content: string;
  citations?: Citation[];
}

/** Metadata for the locally-selected PDF (kept in browser memory only). */
export interface PdfDocumentInfo {
  name: string;
  /** Object URL created via URL.createObjectURL — must be revoked on change. */
  url: string;
  sizeBytes: number;
}
