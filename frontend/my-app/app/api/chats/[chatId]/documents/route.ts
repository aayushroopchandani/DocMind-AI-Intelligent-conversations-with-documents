import { auth } from "@clerk/nextjs/server";
import {
  backendHeaders,
  backendUrl,
  decodeChatId,
  jsonPassthrough,
  sanitizeChatPayload,
} from "@/lib/server/backend";

type Ctx = { params: Promise<{ chatId: string }> };

/** Fetch safe document metadata for one saved chat. */
export async function GET(_req: Request, { params }: Ctx) {
  const { userId } = await auth();
  if (!userId) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { chatId } = await params;
  const backendChatId = decodeChatId(chatId);
  if (!backendChatId) {
    return Response.json({ error: "Invalid chat id" }, { status: 400 });
  }

  const res = await fetch(backendUrl(`/chats/${backendChatId}/documents`), {
    method: "GET",
    headers: backendHeaders(userId),
    cache: "no-store",
  });

  return jsonPassthrough(res, sanitizeChatPayload);
}
