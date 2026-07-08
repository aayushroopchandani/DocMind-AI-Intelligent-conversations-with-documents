import { auth } from "@clerk/nextjs/server";
import { backendHeaders, backendUrl, passthrough } from "@/lib/server/backend";

/** Create a new empty chat owned by the signed-in user. */
export async function POST() {
  const { userId } = await auth();
  if (!userId) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const res = await fetch(backendUrl("/chats"), {
    method: "POST",
    headers: backendHeaders(userId),
  });

  return passthrough(res);
}
