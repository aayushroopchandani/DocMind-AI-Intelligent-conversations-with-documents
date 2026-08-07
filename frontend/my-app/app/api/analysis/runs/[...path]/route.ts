import { auth } from "@clerk/nextjs/server";
import { backendHeaders, backendUrl, passthrough } from "@/lib/server/backend";

type Ctx = { params: Promise<{ path: string[] }> };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const GET_OPERATIONS = new Set(["events", "plan"]);
const POST_OPERATIONS = new Set([
  "approve",
  "reject",
  "cancel",
  "pause",
  "resume",
  "resume-as-new",
]);

function target(path: string[], method: "GET" | "POST"): string | null {
  if (path.length === 1 && method === "GET" && UUID.test(path[0])) {
    return `/analysis/runs/${path[0]}`;
  }
  if (path.length !== 2 || !UUID.test(path[0])) return null;
  const allowed = method === "GET" ? GET_OPERATIONS : POST_OPERATIONS;
  if (!allowed.has(path[1])) return null;
  return `/analysis/runs/${path[0]}/${path[1]}`;
}

export async function GET(req: Request, { params }: Ctx) {
  const { userId } = await auth();
  if (!userId) return Response.json({ error: "Unauthorized" }, { status: 401 });
  const path = (await params).path;
  const backendPath = target(path, "GET");
  if (!backendPath) return Response.json({ error: "Not found" }, { status: 404 });

  const headers = backendHeaders(userId);
  const lastEventId = req.headers.get("last-event-id");
  if (lastEventId) headers.set("last-event-id", lastEventId);
  const res = await fetch(backendUrl(`${backendPath}${new URL(req.url).search}`), {
    headers,
    cache: "no-store",
    signal: req.signal,
  });
  if (path[1] !== "events" || !res.ok || !res.body) return passthrough(res);

  return new Response(res.body, {
    status: res.status,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-store",
      "x-accel-buffering": "no",
    },
  });
}

export async function POST(req: Request, { params }: Ctx) {
  const { userId } = await auth();
  if (!userId) return Response.json({ error: "Unauthorized" }, { status: 401 });
  const path = (await params).path;
  const backendPath = target(path, "POST");
  if (!backendPath) return Response.json({ error: "Not found" }, { status: 404 });

  const headers = backendHeaders(userId, { "content-type": "application/json" });
  const idempotencyKey = req.headers.get("idempotency-key");
  const requestId = req.headers.get("x-request-id");
  if (idempotencyKey) headers.set("idempotency-key", idempotencyKey);
  if (requestId) headers.set("x-request-id", requestId);
  const body = await req.text();
  const res = await fetch(backendUrl(backendPath), {
    method: "POST",
    headers,
    body: body || "{}",
  });
  return passthrough(res);
}
