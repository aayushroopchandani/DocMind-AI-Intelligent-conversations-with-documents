import { auth, currentUser } from "@clerk/nextjs/server";
import { backendHeaders, backendUrl, passthrough } from "@/lib/server/backend";

/**
 * Idempotently mirror the signed-in Clerk user into MongoDB.
 *
 * Called once from the client after authentication. The backend upserts by
 * Clerk id, so repeated calls never create duplicates.
 */
export async function POST() {
  const { userId } = await auth();
  if (!userId) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const user = await currentUser();
  const email =
    user?.primaryEmailAddress?.emailAddress ??
    user?.emailAddresses?.[0]?.emailAddress;

  if (!email) {
    return Response.json({ error: "No email on account" }, { status: 400 });
  }

  const res = await fetch(backendUrl("/users/sync"), {
    method: "POST",
    headers: backendHeaders(userId, { "content-type": "application/json" }),
    body: JSON.stringify({ clerk_user_id: userId, email }),
  });

  return passthrough(res);
}
