import { auth } from "@clerk/nextjs/server";
import { backendHeaders, backendUrl, decodeChatId } from "@/lib/server/backend";

type Ctx = { params: Promise<{ chatId: string; documentId: string }> };

type BackendDocument = {
  _id?: string;
  id?: string;
  document_id?: string;
  filename?: string;
  secure_url?: string;
};

type BackendDocumentsResponse = {
  documents?: BackendDocument[];
};

/** Stream a chat document without exposing its raw storage URL to the client. */
export async function GET(_req: Request, { params }: Ctx) {
  const { userId } = await auth();
  if (!userId) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { chatId, documentId } = await params;
  const backendChatId = decodeChatId(chatId);
  if (!backendChatId) {
    return Response.json({ error: "Invalid chat id" }, { status: 400 });
  }

  const documentsRes = await fetch(backendUrl(`/chats/${backendChatId}/documents`), {
    method: "GET",
    headers: backendHeaders(userId),
    cache: "no-store",
  });

  if (!documentsRes.ok) {
    return Response.json(
      { error: "Unable to load document metadata" },
      { status: documentsRes.status },
    );
  }

  const data = (await documentsRes.json()) as BackendDocumentsResponse;
  const document = data.documents?.find(
    (item) =>
      item._id === documentId ||
      item.id === documentId ||
      item.document_id === documentId,
  );

  if (!document?.secure_url) {
    return Response.json({ error: "Document not found" }, { status: 404 });
  }

  const fileRes = await fetch(document.secure_url, {
    method: "GET",
    cache: "no-store",
  });

  if (!fileRes.ok || !fileRes.body) {
    return Response.json(
      { error: "Unable to load PDF" },
      { status: fileRes.status || 502 },
    );
  }

  return new Response(fileRes.body, {
    status: 200,
    headers: {
      "content-type": fileRes.headers.get("content-type") ?? "application/pdf",
      "cache-control": "private, no-store",
      "content-disposition": `inline; filename="${encodeURIComponent(document.filename ?? "document.pdf")}"`,
    },
  });
}
