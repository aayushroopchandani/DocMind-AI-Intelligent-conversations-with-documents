import "server-only";

/**
 * Server-only helpers for talking to the FastAPI backend.
 *
 * The browser never calls FastAPI directly. Route handlers verify the Clerk
 * session, then forward the authenticated user id (and an optional shared
 * secret) so the backend can trust the request.
 */

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";
const INTERNAL_SECRET = process.env.INTERNAL_API_SECRET ?? "";

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
