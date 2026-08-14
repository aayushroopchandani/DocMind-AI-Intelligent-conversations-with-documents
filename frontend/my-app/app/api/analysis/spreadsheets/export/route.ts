import { auth } from "@clerk/nextjs/server";
import { backendHeaders, backendUrl } from "@/lib/server/backend";

/**
 * Spreadsheet export: posts the workbook to FastAPI and returns the `.xlsx`
 * bytes.
 *
 * The shared `passthrough` helper reads the body as text, which would corrupt
 * a binary payload, so the response is forwarded as an ArrayBuffer here and
 * the download headers are preserved.
 */
export async function POST(req: Request) {
  const { userId } = await auth();
  if (!userId) return Response.json({ error: "Unauthorized" }, { status: 401 });

  const headers = backendHeaders(userId);
  headers.set("content-type", "application/json");

  const res = await fetch(backendUrl("/analysis/spreadsheets/export"), {
    method: "POST",
    headers,
    body: await req.arrayBuffer(),
  });

  if (!res.ok) {
    const detail = await res.text();
    return new Response(detail, {
      status: res.status,
      headers: {
        "content-type": res.headers.get("content-type") ?? "application/json",
      },
    });
  }

  const responseHeaders = new Headers({
    "content-type":
      res.headers.get("content-type") ??
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "cache-control": "no-store",
  });
  const disposition = res.headers.get("content-disposition");
  if (disposition) responseHeaders.set("content-disposition", disposition);

  return new Response(await res.arrayBuffer(), {
    status: res.status,
    headers: responseHeaders,
  });
}
