import type { Metadata } from "next";
import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { ChatWorkspace } from "@/components/chat/chat-workspace";
import { UserSync } from "@/components/auth/user-sync";

export const metadata: Metadata = {
  title: "Chat with PDF — DocMind",
};

/**
 * Protected route. The Clerk proxy already guards `/chat`, but we re-check on
 * the server as defense-in-depth and to redirect unauthenticated users.
 */
export default async function ChatPage() {
  const { userId } = await auth();
  if (!userId) {
    redirect("/sign-in");
  }

  return (
    <>
      {/* Mirrors the signed-in user into MongoDB (idempotent). */}
      <UserSync />
      <ChatWorkspace />
    </>
  );
}
