/**
 * The set of backend analysis-run routes the browser may reach (Phase 9.14.1).
 *
 * This table is the tenant boundary's allowlist. The proxy that uses it holds a
 * Clerk-verified user id and an internal secret, so anything reachable through
 * here is reachable by any signed-in browser session. A path that is not in
 * this table does not exist as far as the browser is concerned.
 *
 * Three rules it enforces, none of which the backend can enforce for us:
 *
 * *Only these paths.* The previous matcher allowed any two-segment path whose
 * second segment was in a small set, which could not express the patch routes
 * at all. Enumerating whole paths instead means adding a backend endpoint does
 * not silently publish it.
 *
 * *Only these shapes.* Every dynamic segment is matched against the value space
 * the backend actually produces — run and patch ids are UUIDs, an operation id
 * is a plain identifier, revisions and chunk indices are bounded integers.
 * Nothing that could carry a `/`, a `?`, a `#` or a percent-escape can match, so
 * no request can steer the backend URL somewhere else.
 *
 * *Only this response handling.* A chunk of patch payload is verified byte for
 * byte against a checksum in the browser, so it must not be decoded and
 * re-encoded on the way through. Each route declares how its body is carried.
 *
 * Kept free of imports so the route table can be verified on its own by
 * `scripts/verify-analysis-routes.mjs`.
 */

export type HttpMethod = "GET" | "POST";

/**
 * How the proxy carries the backend response.
 *
 * - `json`   — buffered and forwarded; the default for control-plane replies.
 * - `stream` — forwarded as an event stream, unbuffered, for SSE.
 * - `binary` — forwarded byte for byte, never decoded. Required wherever the
 *   browser checksums what it received.
 */
export type ResponseKind = "json" | "stream" | "binary";

export interface AnalysisRunRoute {
  method: HttpMethod;
  /**
   * Segments after the run id. A string matches literally; a regular
   * expression matches exactly one segment.
   */
  segments: readonly (string | RegExp)[];
  kind: ResponseKind;
  /** Whether the caller's query string is passed on. Off unless declared. */
  forwardQuery?: boolean;
}

export interface ResolvedRoute {
  /** Absolute backend path, already assembled from validated segments. */
  backendPath: string;
  kind: ResponseKind;
  forwardQuery: boolean;
}

/** Run and patch identifiers are UUIDs (`uuid4()` server-side). */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/**
 * Patch operation ids: `write_result`, `create_target_sheet`,
 * `formula_<column_key>` and their `__inverse` counterparts. Column keys are
 * themselves constrained to `[A-Za-z_][A-Za-z0-9_]*` by the plan schema, so the
 * whole space is plain identifier characters.
 */
const OP_ID = /^[A-Za-z_][A-Za-z0-9_]{0,119}$/;

/** Patch revisions start at 1; chunk indices at 0. Both are bounded. */
const REVISION = /^[1-9][0-9]{0,8}$/;
const CHUNK_INDEX = /^(?:0|[1-9][0-9]{0,8})$/;

export const ANALYSIS_RUN_ROUTES: readonly AnalysisRunRoute[] = [
  // Run lifecycle (Phase 8).
  { method: "GET", segments: [], kind: "json" },
  { method: "GET", segments: ["events"], kind: "stream", forwardQuery: true },
  { method: "GET", segments: ["plan"], kind: "json" },
  { method: "POST", segments: ["approve"], kind: "json" },
  { method: "POST", segments: ["reject"], kind: "json" },
  { method: "POST", segments: ["cancel"], kind: "json" },
  { method: "POST", segments: ["pause"], kind: "json" },
  { method: "POST", segments: ["resume"], kind: "json" },
  { method: "POST", segments: ["resume-as-new"], kind: "json" },

  // What a run executed (Phase 9.14.1).
  { method: "GET", segments: ["execution"], kind: "json" },
  { method: "GET", segments: ["execution", "preview"], kind: "json" },

  // Workbook patch lifecycle (Phase 9.11–9.12). These answer 503 until the
  // deployment sets ANALYSIS_WORKBOOK_PATCHES_READY, but the transport is the
  // same either way — the gate belongs in the backend, not in this table.
  { method: "GET", segments: ["patch"], kind: "json" },
  { method: "POST", segments: ["patch", "context"], kind: "json" },
  { method: "POST", segments: ["patch", "approve"], kind: "json" },
  { method: "POST", segments: ["patch", "reject"], kind: "json" },
  { method: "POST", segments: ["patch", "preflight"], kind: "json" },
  { method: "POST", segments: ["patch", "receipt"], kind: "json" },
  { method: "POST", segments: ["patch", "undo"], kind: "json" },
  {
    method: "GET",
    segments: [
      "patch",
      UUID,
      "revisions",
      REVISION,
      "operations",
      OP_ID,
      "chunks",
      CHUNK_INDEX,
    ],
    // Checksummed in the browser: decoding and re-encoding this body could
    // change the bytes the checksum is computed over.
    kind: "binary",
  },
] as const;

/**
 * Routes bucketed by method and segment count.
 *
 * The table is small, but bucketing turns resolution into a scan of the two or
 * three routes that could possibly match instead of all of them, and it makes
 * the length check implicit rather than repeated per route.
 */
const BY_SHAPE: ReadonlyMap<string, readonly AnalysisRunRoute[]> = (() => {
  const buckets = new Map<string, AnalysisRunRoute[]>();
  for (const route of ANALYSIS_RUN_ROUTES) {
    const key = `${route.method}:${route.segments.length}`;
    const bucket = buckets.get(key);
    if (bucket) bucket.push(route);
    else buckets.set(key, [route]);
  }
  return buckets;
})();

function segmentMatches(pattern: string | RegExp, value: string): boolean {
  return typeof pattern === "string" ? pattern === value : pattern.test(value);
}

/**
 * Resolve one incoming catch-all path to a backend path, or `null`.
 *
 * `path` is the catch-all remainder: `path[0]` is the run id and the rest are
 * the route's own segments. Returning `null` means "no such route" — callers
 * must answer 404 and must not fall back to constructing a path themselves.
 */
export function resolveAnalysisRunRoute(
  path: readonly string[],
  method: HttpMethod,
): ResolvedRoute | null {
  if (path.length === 0) return null;
  const runId = path[0];
  if (!UUID.test(runId)) return null;

  const rest = path.slice(1);
  const candidates = BY_SHAPE.get(`${method}:${rest.length}`);
  if (!candidates) return null;

  for (const route of candidates) {
    let matched = true;
    for (let index = 0; index < rest.length; index += 1) {
      if (!segmentMatches(route.segments[index], rest[index])) {
        matched = false;
        break;
      }
    }
    if (!matched) continue;
    // Assembled from segments that have each been validated above, so the
    // result cannot contain a separator the caller chose.
    const suffix = rest.length > 0 ? `/${rest.join("/")}` : "";
    return {
      backendPath: `/analysis/runs/${runId}${suffix}`,
      kind: route.kind,
      forwardQuery: route.forwardQuery === true,
    };
  }
  return null;
}
