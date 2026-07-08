"use client";

import { useEffect, useRef } from "react";
import { syncUser } from "@/lib/api";

/**
 * Fires once after the authenticated app loads to mirror the Clerk user into
 * MongoDB. The backend upserts by Clerk id, so this is safe to call on every
 * visit — existing users are never duplicated. Renders nothing.
 */
export function UserSync() {
  const hasRun = useRef(false);

  useEffect(() => {
    if (hasRun.current) return;
    hasRun.current = true;
    // Best-effort: a failed sync should never block using the app.
    syncUser().catch(() => {});
  }, []);

  return null;
}
