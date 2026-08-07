import { auth } from "@clerk/nextjs/server";
import {
  backendHeaders,
  backendUrl,
  decodeChatId,
  passthrough,
} from "@/lib/server/backend";

export async function GET(req: Request) {
  const { userId } = await auth();
  if (!userId) return Response.json({ error: "Unauthorized" }, { status: 401 });

  const query = new URL(req.url).search;
  const res = await fetch(backendUrl(`/analysis/runs${query}`), {
    headers: backendHeaders(userId),
    cache: "no-store",
  });
  return passthrough(res);
}

export async function POST(req: Request) {
  const { userId } = await auth();
  if (!userId) return Response.json({ error: "Unauthorized" }, { status: 401 });

  const payload = (await req.json().catch(() => null)) as Record<string, unknown> | null;
  if (!payload) return Response.json({ error: "Invalid JSON body" }, { status: 400 });

  const pdfContext = payload.pdf_context;
  if (pdfContext && typeof pdfContext === "object") {
    const context = { ...(pdfContext as Record<string, unknown>) };
    if (typeof context.chat_id === "string") {
      const decoded = decodeChatId(context.chat_id);
      if (!decoded) return Response.json({ error: "Invalid PDF chat id" }, { status: 400 });
      context.chat_id = decoded;
    }
    payload.pdf_context = context;
  }

  const headers = backendHeaders(userId, { "content-type": "application/json" });
  const idempotencyKey = req.headers.get("idempotency-key");
  const requestId = req.headers.get("x-request-id");
  if (idempotencyKey) headers.set("idempotency-key", idempotencyKey);
  if (requestId) headers.set("x-request-id", requestId);

  const res = await fetch(backendUrl("/analysis/runs"), {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  return passthrough(res);
}
