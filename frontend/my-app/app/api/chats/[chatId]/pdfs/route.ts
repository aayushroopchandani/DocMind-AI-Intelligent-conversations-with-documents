import { auth } from "@clerk/nextjs/server";
import {
  backendHeaders,
  backendUrl,
  decodeChatId,
  jsonPassthrough,
  sanitizeChatPayload,
} from "@/lib/server/backend";

type Ctx = { params: Promise<{ chatId: string }> };

/** Upload one or more PDFs (multipart) to a chat -> Cloudinary + MongoDB. */
export async function POST(req: Request, { params }: Ctx) {
  const { userId } = await auth();
  if (!userId) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { chatId } = await params;
  const backendChatId = decodeChatId(chatId);
  if (!backendChatId) {
    return Response.json({ error: "Invalid chat id" }, { status: 400 });
  }

  // Buffer the raw multipart bytes and forward them verbatim, preserving the
  // original Content-Type (with its boundary). We deliberately avoid two other
  // approaches that fail on Next 16:
  //   - `req.formData()` + re-send: corrupts the boundary ("expected boundary
  //     after body", 500).
  //   - streaming `req.body`: the stream arrives empty/truncated on subsequent
  //     requests (missing `files` field -> 422).
  const headers = backendHeaders(userId);
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  const body = await req.arrayBuffer();

  const res = await fetch(backendUrl(`/chats/${backendChatId}/pdfs`), {
    method: "POST",
    headers,
    body,
  });

  return jsonPassthrough(res, sanitizeChatPayload);
}

/** Detach a shared PDF document from a chat by its MongoDB id. */
export async function DELETE(req: Request, { params }: Ctx) {
  const { userId } = await auth();
  if (!userId) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { chatId } = await params;
  const backendChatId = decodeChatId(chatId);
  if (!backendChatId) {
    return Response.json({ error: "Invalid chat id" }, { status: 400 });
  }

  const pdfId = new URL(req.url).searchParams.get("pdfId");
  if (!pdfId) {
    return Response.json({ error: "Missing pdfId" }, { status: 400 });
  }

  const res = await fetch(
    backendUrl(`/chats/${backendChatId}/pdfs/${encodeURIComponent(pdfId)}`),
    { method: "DELETE", headers: backendHeaders(userId) },
  );

  return jsonPassthrough(res, sanitizeChatPayload);
}
