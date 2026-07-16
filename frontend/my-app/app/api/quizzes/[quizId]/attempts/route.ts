import { auth } from "@clerk/nextjs/server";
import {
  backendHeaders,
  backendUrl,
  encodeChatId,
  jsonPassthrough,
} from "@/lib/server/backend";

type Context = { params: Promise<{ quizId: string }> };

function sanitizeAttempt(payload: unknown): unknown {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return payload;
  const attempt = { ...(payload as Record<string, unknown>) };
  if (typeof attempt.chat_id === "string") {
    attempt.chat_id = encodeChatId(attempt.chat_id);
  }
  return attempt;
}

export async function POST(request: Request, { params }: Context) {
  const { userId } = await auth();
  if (!userId) return Response.json({ error: "Unauthorized" }, { status: 401 });

  const { quizId } = await params;
  const headers = backendHeaders(userId, { "content-type": "application/json" });
  const response = await fetch(
    backendUrl(`/quizzes/${encodeURIComponent(quizId)}/attempts`),
    { method: "POST", headers, body: await request.text() },
  );
  return jsonPassthrough(response, sanitizeAttempt);
}
