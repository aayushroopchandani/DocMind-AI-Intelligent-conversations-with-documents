import type {
  BackendCitation,
  BackendFinalData,
  ChatApiResponse,
  Citation,
  StreamEvent,
  StructuredAnswer,
} from "@/lib/types";

/**
 * Client-side wrappers around the Next.js route handlers (which proxy to the
 * FastAPI backend after verifying the Clerk session). The browser only ever
 * calls same-origin `/api/*` routes.
 */

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    return (data?.detail as string) || (data?.error as string) || res.statusText;
  } catch {
    return res.statusText || "Request failed";
  }
}

/** Idempotently mirror the signed-in user into MongoDB. Safe to call once per session. */
export async function syncUser(): Promise<void> {
  const res = await fetch("/api/user/sync", { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res));
}

/** Create a new empty chat for the signed-in user. */
export async function createChat(): Promise<ChatApiResponse> {
  const res = await fetch("/api/chats", { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** Upload PDFs to a chat. They're stored in Cloudinary and recorded in MongoDB. */
export async function uploadPdfs(
  chatId: string,
  files: File[],
): Promise<ChatApiResponse> {
  const form = new FormData();
  for (const file of files) form.append("files", file);

  const res = await fetch(`/api/chats/${chatId}/pdfs`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/* ------------------------------------------------------------------ */
/* Streaming chat                                                      */
/* ------------------------------------------------------------------ */

export function mapCitation(raw: BackendCitation): Citation {
  return {
    citationId: raw.citation_id,
    documentId: raw.document_id,
    documentName: raw.document_name,
    pageNumber: raw.page_number,
    excerpt: raw.excerpt,
  };
}

export function mapStructured(data: BackendFinalData): StructuredAnswer {
  return {
    answerFound: data.answer_found,
    status: data.status,
    confidenceScore: data.confidence_score,
    followUpQuestions: data.follow_up_questions ?? [],
    contributions: (data.document_contributions ?? []).map((c) => ({
      documentId: c.document_id,
      documentName: c.document_name,
      contribution: c.contribution,
      relevantPages: c.relevant_pages ?? [],
      citationIds: c.citation_ids ?? [],
    })),
  };
}

export interface StreamChatCallbacks {
  onStatus: (message: string) => void;
  onToken: (text: string) => void;
  onCitations: (citations: Citation[]) => void;
  onFinal: (structured: StructuredAnswer, citations: Citation[]) => void;
  onError: (message: string) => void;
}

/**
 * Ask a question about the chat's PDFs and consume the SSE answer stream.
 *
 * The endpoint is a POST, so we read `response.body` manually with a reader +
 * TextDecoder and split on the SSE frame delimiter (`\n\n`). Resolves after
 * the `done` event; rejects on network failure or abort.
 */
export async function streamChat(
  chatId: string,
  question: string,
  documentIds: string[] | undefined,
  callbacks: StreamChatCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/chats/${chatId}/stream`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, document_ids: documentIds ?? null }),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(await parseError(res));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const handleEvent = (event: StreamEvent) => {
    switch (event.type) {
      case "status":
        callbacks.onStatus(event.message);
        break;
      case "token":
        callbacks.onToken(event.content);
        break;
      case "citations":
        callbacks.onCitations(event.citations.map(mapCitation));
        break;
      case "final":
        callbacks.onFinal(
          mapStructured(event.data),
          (event.data.citations ?? []).map(mapCitation),
        );
        break;
      case "error":
        callbacks.onError(event.message);
        break;
      case "done":
        break;
    }
  };

  const processBuffer = () => {
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data:")) continue;
        try {
          handleEvent(JSON.parse(line.slice(5).trim()) as StreamEvent);
        } catch {
          // Ignore malformed frames rather than killing the stream.
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    processBuffer();
  }
  buffer += decoder.decode();
  processBuffer();
}

/** Remove a PDF from a chat (deletes it from Cloudinary too). */
export async function deletePdf(
  chatId: string,
  publicId: string,
): Promise<ChatApiResponse> {
  const res = await fetch(
    `/api/chats/${chatId}/pdfs?publicId=${encodeURIComponent(publicId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
