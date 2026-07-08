import { auth } from "@clerk/nextjs/server";
import { backendHeaders, backendUrl, passthrough } from "@/lib/server/backend";

type Ctx = { params: Promise<{ chatId: string }> };

/**
 * Streaming ask endpoint. Verifies the Clerk session, then pipes the FastAPI
 * SSE response through to the browser without buffering so tokens render as
 * soon as they're generated. Aborting the client fetch cancels the upstream
 * request too (via req.signal).
 */
export async function POST(req: Request, { params }: Ctx) {
  const { userId } = await auth();
  if (!userId) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { chatId } = await params;

  const headers = backendHeaders(userId);
  headers.set("content-type", "application/json");

  const res = await fetch(backendUrl(`/chats/${chatId}/stream`), {
    method: "POST",
    headers,
    body: await req.text(),
    signal: req.signal,
  });

  // Non-streaming errors (404/403/400) come back as JSON — forward as-is.
  if (!res.ok || !res.body) {
    return passthrough(res);
  }

  return new Response(res.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
