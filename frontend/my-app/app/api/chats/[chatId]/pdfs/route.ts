import { auth } from "@clerk/nextjs/server";
import { backendHeaders, backendUrl, passthrough } from "@/lib/server/backend";

type Ctx = { params: Promise<{ chatId: string }> };

/** Upload one or more PDFs (multipart) to a chat -> Cloudinary + MongoDB. */
export async function POST(req: Request, { params }: Ctx) {
  const { userId } = await auth();
  if (!userId) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { chatId } = await params;

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

  const res = await fetch(backendUrl(`/chats/${chatId}/pdfs`), {
    method: "POST",
    headers,
    body,
  });

  return passthrough(res);
}

/** Remove a single PDF from a chat. public_id is passed as a query param. */
export async function DELETE(req: Request, { params }: Ctx) {
  const { userId } = await auth();
  if (!userId) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { chatId } = await params;
  const publicId = new URL(req.url).searchParams.get("publicId");
  if (!publicId) {
    return Response.json({ error: "Missing publicId" }, { status: 400 });
  }

  // public_id contains slashes (folder path) and may contain spaces — encode
  // each segment but preserve the slashes for the backend's `:path` matcher.
  const encodedPublicId = publicId.split("/").map(encodeURIComponent).join("/");

  const res = await fetch(
    backendUrl(`/chats/${chatId}/pdfs/${encodedPublicId}`),
    { method: "DELETE", headers: backendHeaders(userId) },
  );

  return passthrough(res);
}
