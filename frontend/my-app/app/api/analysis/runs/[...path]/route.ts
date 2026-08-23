import { auth } from "@clerk/nextjs/server";
import {
  resolveAnalysisRunRoute,
  type ResolvedRoute,
} from "@/lib/server/analysis-routes";
import {
  backendHeaders,
  backendUrl,
  passthrough,
  streamThrough,
} from "@/lib/server/backend";

/**
 * The browser's only door to the backend's analysis-run endpoints.
 *
 * Clerk verifies the session here; the backend is then told which user is
 * asking and re-checks ownership at every repository query. What this handler
 * owns is narrower and all of it is refusal: which paths exist at all, which
 * request headers cross, and how each response body is carried back.
 *
 * The allowlist itself lives in `lib/server/analysis-routes.ts`, where it can
 * be read as one table and verified on its own.
 */

type Ctx = { params: Promise<{ path: string[] }> };

/** Correlation id, echoed by the backend into its structured logs. */
const TRACE_HEADER = "x-request-id";

function unauthorized(): Response {
  return Response.json({ error: "Unauthorized" }, { status: 401 });
}

function notFound(): Response {
  return Response.json({ error: "Not found" }, { status: 404 });
}

/**
 * Build the backend URL, appending the caller's query string only for routes
 * that declare one. Everything else takes no parameters, so dropping the query
 * keeps a stray one from reaching an endpoint that never expected it.
 */
function targetUrl(route: ResolvedRoute, req: Request): string {
  const search = route.forwardQuery ? new URL(req.url).search : "";
  return backendUrl(`${route.backendPath}${search}`);
}

/** Server-sent events must not be buffered by any hop in front of them. */
function eventStream(res: Response): Response {
  return new Response(res.body, {
    status: res.status,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-store",
      "x-accel-buffering": "no",
    },
  });
}

export async function GET(req: Request, { params }: Ctx) {
  const { userId } = await auth();
  if (!userId) return unauthorized();

  const route = resolveAnalysisRunRoute((await params).path, "GET");
  if (!route) return notFound();

  const headers = backendHeaders(userId);
  const lastEventId = req.headers.get("last-event-id");
  const traceId = req.headers.get(TRACE_HEADER);
  if (lastEventId) headers.set("last-event-id", lastEventId);
  if (traceId) headers.set(TRACE_HEADER, traceId);

  const res = await fetch(targetUrl(route, req), {
    headers,
    cache: "no-store",
    // Abandoning the request must close the upstream one too, or a dropped
    // SSE client would hold a backend connection slot open indefinitely.
    signal: req.signal,
  });

  if (route.kind === "stream" && res.ok && res.body) return eventStream(res);
  // A failed stream request carries a JSON error, so it goes back as one.
  if (route.kind === "binary") return streamThrough(res);
  return passthrough(res);
}

export async function POST(req: Request, { params }: Ctx) {
  const { userId } = await auth();
  if (!userId) return unauthorized();

  const route = resolveAnalysisRunRoute((await params).path, "POST");
  if (!route) return notFound();

  const headers = backendHeaders(userId, { "content-type": "application/json" });
  const idempotencyKey = req.headers.get("idempotency-key");
  const traceId = req.headers.get(TRACE_HEADER);
  // Every mutating analysis endpoint is idempotent on this key, and a patch
  // decision is bound to hashes the server computed. Dropping the key here
  // would turn a safe retry into a second decision.
  if (idempotencyKey) headers.set("idempotency-key", idempotencyKey);
  if (traceId) headers.set(TRACE_HEADER, traceId);

  // Read as bytes rather than text: a patch context can approach the backend's
  // 6 MiB request cap, and decoding it to a JS string on the way through would
  // cost about twice that in heap for no benefit — nothing here inspects it.
  const body = await req.arrayBuffer();

  const res = await fetch(targetUrl(route, req), {
    method: "POST",
    headers,
    body: body.byteLength > 0 ? body : "{}",
    signal: req.signal,
  });
  return passthrough(res);
}
