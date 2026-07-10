"use client";

import { useState } from "react";
import {
  BrainCircuit,
  ChevronDown,
  FileText,
  Folder,
  MessageCircle,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Wrench,
  Loader2,
  PencilLine,
  ShieldCheck,
  SquarePlay,
  GraduationCap,
  Download,
  Smartphone,
} from "lucide-react";
import type { ChatApiResponse } from "@/lib/types";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
} from "@/components/ui/collapsible";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface ChatHistorySidebarProps {
  chats: ChatApiResponse[];
  activeChatId: string | null;
  collapsed: boolean;
  loading: boolean;
  loadingChatId: string | null;
  error: string | null;
  onSelectChat: (chat: ChatApiResponse) => void;
  onNewChat: () => void;
  onToggleCollapsed: () => void;
}

const tools = [
  { label: "AI Writer", icon: PencilLine },
  { label: "AI Detector", icon: ShieldCheck },
  { label: "YouTube Chat", icon: SquarePlay },
  { label: "Research", icon: GraduationCap },
  { label: "Mac app", icon: Download },
  { label: "Mobile app", icon: Smartphone },
];

function titleForChat(chat: ChatApiResponse): string {
  const firstDocument = chat.documents?.[0]?.filename;
  if (firstDocument) return firstDocument.replace(/\.pdf$/i, "");

  const firstUserMessage = chat.conversation?.find(
    (message) => message.role === "user",
  );
  if (firstUserMessage?.content) return firstUserMessage.content;

  return "Untitled chat";
}

function subtitleForChat(chat: ChatApiResponse): string {
  const count = chat.documents?.length ?? 0;
  if (count > 0) return `${count} PDF${count === 1 ? "" : "s"}`;
  if (chat.conversation?.length) return `${chat.conversation.length} messages`;
  return "Empty chat";
}

function ChatSkeleton() {
  return (
    <div className="space-y-2 px-1">
      {[0, 1, 2].map((item) => (
        <div
          key={item}
          className="rounded-lg border border-border/60 bg-card/30 p-2"
        >
          <Skeleton className="mb-2 h-3.5 w-11/12" />
          <Skeleton className="h-2.5 w-1/2" />
        </div>
      ))}
    </div>
  );
}

function CollapsedRail({
  onToggleCollapsed,
  onNewChat,
}: Pick<ChatHistorySidebarProps, "onToggleCollapsed" | "onNewChat">) {
  return (
    <aside className="flex h-full min-h-0 flex-col items-center border-r border-border bg-sidebar py-2 text-sidebar-foreground">
      <div className="flex flex-col items-center gap-2">
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          onClick={onNewChat}
          aria-label="New chat"
          title="New chat"
          className="text-muted-foreground hover:text-foreground"
        >
          <Plus className="size-5" />
        </Button>
        <button
          type="button"
          aria-label="Chats"
          title="Chats"
          onClick={onToggleCollapsed}
          className="inline-flex size-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <MessageCircle className="size-5" />
        </button>
        <button
          type="button"
          aria-label="Folders"
          title="Folders"
          onClick={onToggleCollapsed}
          className="inline-flex size-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Folder className="size-5" />
        </button>
        <button
          type="button"
          aria-label="Tools"
          title="Tools"
          onClick={onToggleCollapsed}
          className="inline-flex size-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Wrench className="size-5" />
        </button>
      </div>

      <button
        type="button"
        onClick={onToggleCollapsed}
        aria-label="Expand sidebar"
        title="DocMind"
        className="ai-avatar mt-auto inline-flex size-9 items-center justify-center rounded-lg"
      >
        <BrainCircuit className="size-4" />
      </button>

      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        onClick={onToggleCollapsed}
        aria-label="Expand sidebar"
        title="Expand sidebar"
        className="mt-1 text-muted-foreground hover:text-foreground"
      >
        <PanelLeftOpen className="size-4" />
      </Button>
    </aside>
  );
}

export function ChatHistorySidebar({
  chats,
  activeChatId,
  collapsed,
  loading,
  loadingChatId,
  error,
  onSelectChat,
  onNewChat,
  onToggleCollapsed,
}: ChatHistorySidebarProps) {
  const [accordionValue, setAccordionValue] = useState([
    "chats",
    "folders",
    "tools",
  ]);

  if (collapsed) {
    return (
      <CollapsedRail
        onToggleCollapsed={onToggleCollapsed}
        onNewChat={onNewChat}
      />
    );
  }

  return (
    <Collapsible
      open={!collapsed}
      onOpenChange={(open) => {
        if (!open) onToggleCollapsed();
      }}
    >
      <aside className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden border-r border-border bg-sidebar text-sidebar-foreground">
        <div className="flex shrink-0 items-center gap-2 border-b border-border p-3">
          <Button
            type="button"
            variant="outline"
            size="lg"
            onClick={onNewChat}
            className="h-10 min-w-0 flex-1 justify-start gap-2 border-foreground/15 bg-background/40 text-foreground hover:bg-muted"
          >
            <Plus className="size-4" />
            New Chat
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={onToggleCollapsed}
            aria-label="Collapse sidebar"
            title="Collapse sidebar"
            className="text-muted-foreground hover:text-foreground"
          >
            <PanelLeftClose className="size-4" />
          </Button>
        </div>

        <CollapsibleContent className="scrollbar-thin min-h-0 flex-1 overflow-y-auto p-3">
          <Accordion
            value={accordionValue}
            onValueChange={setAccordionValue}
            className="space-y-5"
          >
            <AccordionItem value="chats">
              <AccordionTrigger
                itemValue="chats"
                className="mb-2 flex w-full items-center justify-between rounded-lg px-1 py-1 text-left text-sm font-medium text-foreground"
              >
                <span className="inline-flex min-w-0 items-center gap-2">
                  <MessageCircle className="size-4 text-[color:var(--accent-cyan)]" />
                  Chats
                </span>
                <ChevronDown className="size-4 text-muted-foreground" />
              </AccordionTrigger>
              <AccordionContent itemValue="chats">
                {loading ? <ChatSkeleton /> : null}

                {!loading && error ? (
                  <p className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                    {error}
                  </p>
                ) : null}

                {!loading && !error && chats.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
                    No saved chats yet.
                  </div>
                ) : null}

                {!loading && !error && chats.length > 0 ? (
                  <div className="space-y-1">
                    {chats.map((chat) => {
                      const active = chat.id === activeChatId;
                      const hydrating = chat.id === loadingChatId;

                      return (
                        <button
                          key={chat.id}
                          type="button"
                          onClick={() => onSelectChat(chat)}
                          className={cn(
                            "group flex w-full min-w-0 items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors",
                            active
                              ? "border-[color:var(--accent-cyan)]/45 bg-[color:var(--accent-cyan)]/10 text-foreground"
                              : "border-transparent text-muted-foreground hover:border-border hover:bg-muted/60 hover:text-foreground",
                          )}
                        >
                          <FileText className="size-4 shrink-0" />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium">
                              {titleForChat(chat)}
                            </span>
                            <span className="block truncate text-[11px] text-muted-foreground">
                              {subtitleForChat(chat)}
                            </span>
                          </span>
                          {hydrating ? (
                            <Loader2 className="size-3.5 shrink-0 animate-spin text-[color:var(--accent-cyan)]" />
                          ) : null}
                        </button>
                      );
                    })}
                  </div>
                ) : null}
              </AccordionContent>
            </AccordionItem>

            <AccordionItem value="folders">
              <AccordionTrigger
                itemValue="folders"
                className="mb-2 flex w-full items-center justify-between rounded-lg px-1 py-1 text-left text-sm font-medium text-foreground"
              >
                <span className="inline-flex min-w-0 items-center gap-2">
                  <Folder className="size-4 text-[color:var(--accent-amber)]" />
                  Folders
                </span>
                <ChevronDown className="size-4 text-muted-foreground" />
              </AccordionTrigger>
              <AccordionContent itemValue="folders">
                <button
                  type="button"
                  disabled
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-sm text-muted-foreground/70"
                >
                  <Plus className="size-4" />
                  New folder
                </button>
              </AccordionContent>
            </AccordionItem>

            <AccordionItem value="tools">
              <AccordionTrigger
                itemValue="tools"
                className="mb-2 flex w-full items-center justify-between rounded-lg px-1 py-1 text-left text-sm font-medium text-foreground"
              >
                <span className="inline-flex min-w-0 items-center gap-2">
                  <Wrench className="size-4 text-[color:var(--accent-violet)]" />
                  Tools
                </span>
                <ChevronDown className="size-4 text-muted-foreground" />
              </AccordionTrigger>
              <AccordionContent itemValue="tools">
                <div className="space-y-1">
                  {tools.map((tool) => (
                    <button
                      key={tool.label}
                      type="button"
                      disabled
                      className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm text-muted-foreground/75"
                    >
                      <tool.icon className="size-4 shrink-0" />
                      <span className="truncate">{tool.label}</span>
                    </button>
                  ))}
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </CollapsibleContent>
      </aside>
    </Collapsible>
  );
}
