import type { Metadata } from "next";
import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { ChatWorkspace } from "@/components/chat/chat-workspace";
import { UserSync } from "@/components/auth/user-sync";

type PageProps = { params: Promise<{ chatId: string }> };

export const metadata: Metadata = {
  title: "Chat with PDF — DocMind",
};

export default async function SavedChatPage({ params }: PageProps) {
  const { userId } = await auth();
  if (!userId) {
    redirect("/sign-in");
  }

  const { chatId } = await params;

  return (
    <>
      <UserSync />
      <ChatWorkspace initialChatId={chatId} />
    </>
  );
}
