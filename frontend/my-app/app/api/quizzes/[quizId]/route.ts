import { auth } from "@clerk/nextjs/server";
import {
  backendHeaders,
  backendUrl,
  jsonPassthrough,
} from "@/lib/server/backend";

type Context = { params: Promise<{ quizId: string }> };

export async function GET(_request: Request, { params }: Context) {
  const { userId } = await auth();
  if (!userId) return Response.json({ error: "Unauthorized" }, { status: 401 });

  const { quizId } = await params;
  const response = await fetch(
    backendUrl(`/quizzes/${encodeURIComponent(quizId)}`),
    { headers: backendHeaders(userId), cache: "no-store" },
  );
  return jsonPassthrough(response);
}
