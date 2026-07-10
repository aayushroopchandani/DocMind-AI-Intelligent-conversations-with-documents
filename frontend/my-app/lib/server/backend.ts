import "server-only";

import {
  createCipheriv,
  createDecipheriv,
  createHash,
  createHmac,
} from "node:crypto";

/**
 * Server-only helpers for talking to the FastAPI backend.
 *
 * The browser never calls FastAPI directly. Route handlers verify the Clerk
 * session, then forward the authenticated user id (and an optional shared
 * secret) so the backend can trust the request.
 */

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";
const INTERNAL_SECRET = process.env.INTERNAL_API_SECRET ?? "";
const CHAT_TOKEN_SECRET =
  process.env.CHAT_TOKEN_SECRET ??
  INTERNAL_SECRET ??
  process.env.CLERK_SECRET_KEY ??
  process.env.NEXTAUTH_SECRET ??
  "docmind-local-dev-chat-token-key";
const CHAT_TOKEN_PREFIX = "dm1";

function chatTokenKey(): Buffer {
  return createHash("sha256").update(CHAT_TOKEN_SECRET).digest();
}

export function encodeChatId(chatId: string): string {
  const key = chatTokenKey();
  const iv = createHmac("sha256", key).update(chatId).digest().subarray(0, 12);
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  const ciphertext = Buffer.concat([
    cipher.update(chatId, "utf8"),
    cipher.final(),
  ]);
  const tag = cipher.getAuthTag();

  return `${CHAT_TOKEN_PREFIX}.${Buffer.concat([iv, tag, ciphertext]).toString("base64url")}`;
}

export function decodeChatId(token: string): string | null {
  if (!token.startsWith(`${CHAT_TOKEN_PREFIX}.`)) return null;

  try {
    const payload = Buffer.from(token.slice(CHAT_TOKEN_PREFIX.length + 1), "base64url");
    const iv = payload.subarray(0, 12);
    const tag = payload.subarray(12, 28);
    const ciphertext = payload.subarray(28);
    const decipher = createDecipheriv("aes-256-gcm", chatTokenKey(), iv);
    decipher.setAuthTag(tag);

    return Buffer.concat([
      decipher.update(ciphertext),
      decipher.final(),
    ]).toString("utf8");
  } catch {
    return null;
  }
}

export function backendUrl(path: string): string {
  return `${BACKEND_URL}${path}`;
}

/** Headers identifying the authenticated user to the backend. */
export function backendHeaders(userId: string, extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  headers.set("X-User-Id", userId);
  if (INTERNAL_SECRET) headers.set("X-Internal-Secret", INTERNAL_SECRET);
  return headers;
}

/** Forward a backend Response through to the client, preserving status/body. */
export async function passthrough(res: Response): Promise<Response> {
  const body = await res.text();
  return new Response(body, {
    status: res.status,
    headers: { "content-type": res.headers.get("content-type") ?? "application/json" },
  });
}

function sanitizeDocument(document: unknown): unknown {
  if (!document || typeof document !== "object") return document;

  const safeDocument = { ...(document as Record<string, unknown>) };
  delete safeDocument.secure_url;
  delete safeDocument.public_id;
  delete safeDocument.private_id;
  delete safeDocument.chat_ids;

  return safeDocument;
}

function sanitizeChatObject(data: Record<string, unknown>): Record<string, unknown> {
  const safeData: Record<string, unknown> = { ...data };

  if (typeof safeData.id === "string") {
    safeData.id = encodeChatId(safeData.id);
  }
  if (typeof safeData.chat_id === "string") {
    safeData.chat_id = encodeChatId(safeData.chat_id);
  }
  if (Array.isArray(safeData.documents)) {
    safeData.documents = safeData.documents.map(sanitizeDocument);
  }

  return safeData;
}

export function sanitizeChatPayload(payload: unknown): unknown {
  if (Array.isArray(payload)) return payload.map(sanitizeChatPayload);
  if (!payload || typeof payload !== "object") return payload;

  return sanitizeChatObject(payload as Record<string, unknown>);
}

export async function jsonPassthrough(
  res: Response,
  transform: (payload: unknown) => unknown = (payload) => payload,
): Promise<Response> {
  const payload = await res.json().catch(() => null);
  return Response.json(transform(payload), { status: res.status });
}
