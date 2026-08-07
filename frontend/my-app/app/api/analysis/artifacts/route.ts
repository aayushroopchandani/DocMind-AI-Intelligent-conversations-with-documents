import { auth } from "@clerk/nextjs/server";
import { backendHeaders, backendUrl, passthrough } from "@/lib/server/backend";

export async function POST(req: Request) {
  const { userId } = await auth();
  if (!userId) return Response.json({ error: "Unauthorized" }, { status: 401 });

  const headers = backendHeaders(userId);
  const contentType = req.headers.get("content-type");
  const idempotencyKey = req.headers.get("idempotency-key");
  if (contentType) headers.set("content-type", contentType);
  if (idempotencyKey) headers.set("idempotency-key", idempotencyKey);
  const res = await fetch(backendUrl("/analysis/artifacts"), {
    method: "POST",
    headers,
    body: await req.arrayBuffer(),
  });
  return passthrough(res);
}
