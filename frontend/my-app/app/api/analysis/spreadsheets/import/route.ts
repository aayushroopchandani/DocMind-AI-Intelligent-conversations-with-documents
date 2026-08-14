import { auth } from "@clerk/nextjs/server";
import { backendHeaders, backendUrl, passthrough } from "@/lib/server/backend";

/**
 * Spreadsheet import: forwards the uploaded file to FastAPI, which validates
 * and converts it. The body is streamed through untouched — parsing a
 * multipart upload here only to rebuild it would double the memory cost.
 */
export async function POST(req: Request) {
  const { userId } = await auth();
  if (!userId) return Response.json({ error: "Unauthorized" }, { status: 401 });

  const headers = backendHeaders(userId);
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  const res = await fetch(backendUrl("/analysis/spreadsheets/import"), {
    method: "POST",
    headers,
    body: await req.arrayBuffer(),
  });
  return passthrough(res);
}
