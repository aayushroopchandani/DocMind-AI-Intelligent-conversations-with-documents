import { auth } from "@clerk/nextjs/server";
import {
  backendHeaders,
  backendUrl,
  jsonPassthrough,
  sanitizeChatPayload,
} from "@/lib/server/backend";

/** List saved chats owned by the signed-in user. */
export async function GET() {
  const { userId } = await auth();
  if (!userId) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const res = await fetch(backendUrl(`/chats/${encodeURIComponent(userId)}/chats`), {
    method: "GET",
    headers: backendHeaders(userId),
    cache: "no-store",
  });

  return jsonPassthrough(res, sanitizeChatPayload);
}

/** Create a new empty chat owned by the signed-in user. */
export async function POST() {
  const { userId } = await auth();
  if (!userId) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const res = await fetch(backendUrl("/chats"), {
    method: "POST",
    headers: backendHeaders(userId),
  });

  return jsonPassthrough(res, sanitizeChatPayload);
}
