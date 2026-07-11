"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { AlertCircle, FileText, Loader2, MessagesSquare } from "lucide-react";
import type {
  ChatApiResponse,
  ChatMessage,
  Citation,
  PdfDoc,
  PdfDocumentRecord,
} from "@/lib/types";
import {
  createChat,
  deletePdf,
  documentFileUrl,
  getChatDocuments,
  getChats,
  mapPersistedConversation,
  streamChat,
  uploadPdfs,
} from "@/lib/api";
import { validatePdfFiles } from "@/lib/pdf";
import { ChatHeader } from "@/components/chat/chat-header";
import { ChatHistorySidebar } from "@/components/chat/chat-history-sidebar";
import { PdfUploader } from "@/components/chat/pdf-uploader";
import { PdfTabs } from "@/components/chat/pdf-tabs";
import { PdfControls } from "@/components/chat/pdf-controls";
import { ChatPanel } from "@/components/chat/chat-panel";
import { ResizableSplit } from "@/components/ui/resizable";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const PdfViewer = dynamic(
  () => import("@/components/chat/pdf-viewer").then((m) => m.PdfViewer),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading viewer…
      </div>
    ),
  },
);

const MAX_FILES = 4;
const MIN_SCALE = 0.5;
const MAX_SCALE = 3;
const SCALE_STEP = 0.2;

const SIDEBAR_COLLAPSED_WIDTH = 56;
const SIDEBAR_DEFAULT_WIDTH = 292;
const SIDEBAR_MIN_WIDTH = 232;
const SIDEBAR_MAX_WIDTH = 360;
const SIDEBAR_COLLAPSE_AT = 160;

const PDF_DEFAULT_WIDTH = 720;
const PDF_MIN_WIDTH = 380;
const PDF_MAX_WIDTH = 980;
const CHAT_MIN_WIDTH = 360;

type MobileTab = "pdf" | "chat";
type SelectChatOptions = { replace?: boolean; skipNavigation?: boolean };

function createId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function documentDbId(document: PdfDocumentRecord): string | undefined {
  return document._id ?? document.id;
}

function revokeBlobUrls(docs: PdfDoc[]) {
  for (const doc of docs) {
    if (doc.url.startsWith("blob:")) URL.revokeObjectURL(doc.url);
  }
}

function pdfDocFromRecord(chatId: string, document: PdfDocumentRecord): PdfDoc {
  const dbId = documentDbId(document);

  return {
    id: dbId ?? document.document_id,
    name: document.filename || "document.pdf",
    sizeBytes: document.bytes ?? 0,
    url: documentFileUrl(chatId, document),
    status: document.ingestion_status === "ready" ? "ready" : "uploading",
    numPages: document.pages ?? 0,
    lastPage: 1,
    scale: 1,
    fitWidth: true,
    documentDbId: dbId,
    documentId: document.document_id,
    cloudinaryPages: document.pages ?? undefined,
  };
}

function chatTimestamp(chat: ChatApiResponse): number {
  const value = chat.updated_at ?? chat.created_at;
  return value ? new Date(value).getTime() : 0;
}

function sortRecentChats(chats: ChatApiResponse[]): ChatApiResponse[] {
  return [...chats].sort((a, b) => chatTimestamp(b) - chatTimestamp(a));
}

function PdfLoadingSkeleton() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex gap-2 border-b border-border bg-card/50 p-2">
        <Skeleton className="h-11 w-44 rounded-xl" />
        <Skeleton className="h-11 w-40 rounded-xl" />
      </div>
      <div className="flex items-center justify-between border-b border-border bg-card/60 px-3 py-2">
        <Skeleton className="h-8 w-36" />
        <Skeleton className="h-8 w-28" />
      </div>
      <div className="grid flex-1 place-items-center bg-background/40 p-6">
        <div className="w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-xl shadow-black/20">
          <Skeleton className="mb-4 h-5 w-2/3" />
          <Skeleton className="mb-2 h-3 w-full" />
          <Skeleton className="mb-2 h-3 w-11/12" />
          <Skeleton className="mb-6 h-3 w-4/5" />
          <Skeleton className="h-72 w-full rounded-lg" />
        </div>
      </div>
    </div>
  );
}

/**
 * Client-side orchestrator for the multi-PDF chat workspace.
 *
 * Owns saved chat history, current PDF tabs, active chat id, conversation
 * state, and the resizable desktop shell.
 */
export function ChatWorkspace({ initialChatId }: { initialChatId?: string }) {
  const router = useRouter();
  const [docs, setDocs] = useState<PdfDoc[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isResponding, setIsResponding] = useState(false);
  const [isLoadingConversation, setIsLoadingConversation] = useState(false);
  const [mobileTab, setMobileTab] = useState<MobileTab>("pdf");

  const [savedChats, setSavedChats] = useState<ChatApiResponse[]>([]);
  const [chatsLoading, setChatsLoading] = useState(true);
  const [chatsError, setChatsError] = useState<string | null>(null);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [loadingChatId, setLoadingChatId] = useState<string | null>(null);

  const [sidebarWidth, setSidebarWidth] = useState(SIDEBAR_DEFAULT_WIDTH);
  const [pdfWidth, setPdfWidth] = useState(PDF_DEFAULT_WIDTH);

  // Lazily-created chat id + a guard so concurrent uploads share one creation.
  const chatIdRef = useRef<string | null>(null);
  const chatCreationRef = useRef<Promise<string> | null>(null);
  // Abort controller for the in-flight answer stream (stop button / unmount).
  const abortRef = useRef<AbortController | null>(null);
  // Mirrors for async callbacks without stale closures.
  const docsRef = useRef<PdfDoc[]>([]);
  const selectionRef = useRef(0);
  const initialChatLoadedRef = useRef(false);

  useEffect(() => {
    docsRef.current = docs;
  }, [docs]);

  const loadChats = useCallback(async () => {
    setChatsLoading(true);
    setChatsError(null);

    try {
      const chats = await getChats();
      setSavedChats(sortRecentChats(chats));
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Could not load saved chats.";
      if (/user not found/i.test(message)) {
        setSavedChats([]);
      } else {
        setChatsError(message);
      }
    } finally {
      setChatsLoading(false);
    }
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      void loadChats();
    }, 0);

    return () => window.clearTimeout(timeout);
  }, [loadChats]);

  // Revoke every object URL + abort any in-flight stream on unmount.
  useEffect(() => {
    return () => {
      revokeBlobUrls(docsRef.current);
      abortRef.current?.abort();
    };
  }, []);

  const activeDoc = docs.find((d) => d.id === activeId) ?? null;
  const remaining = MAX_FILES - docs.length;
  const sidebarCollapsed = sidebarWidth <= SIDEBAR_COLLAPSE_AT;

  const patchDoc = useCallback((id: string, patch: Partial<PdfDoc>) => {
    setDocs((prev) => prev.map((d) => (d.id === id ? { ...d, ...patch } : d)));
  }, []);

  const upsertSavedChat = useCallback((chat: ChatApiResponse) => {
    setSavedChats((prev) => {
      const rest = prev.filter((item) => item.id !== chat.id);
      return sortRecentChats([chat, ...rest]);
    });
  }, []);

  const ensureChat = useCallback(async (): Promise<string> => {
    if (chatIdRef.current) return chatIdRef.current;
    if (chatCreationRef.current) return chatCreationRef.current;

    const promise = createChat()
      .then((chat) => {
        chatIdRef.current = chat.id;
        setActiveChatId(chat.id);
        upsertSavedChat(chat);
        router.replace(`/chat/${encodeURIComponent(chat.id)}`);
        return chat.id;
      })
      .finally(() => {
        chatCreationRef.current = null;
      });
    chatCreationRef.current = promise;
    return promise;
  }, [router, upsertSavedChat]);

  const handleNewChat = useCallback(() => {
    abortRef.current?.abort();
    revokeBlobUrls(docsRef.current);
    selectionRef.current += 1;
    chatIdRef.current = null;
    chatCreationRef.current = null;
    setDocs([]);
    setActiveId(null);
    setMessages([]);
    setInput("");
    setUploadError(null);
    setActiveChatId(null);
    setLoadingChatId(null);
    setIsLoadingConversation(false);
    setIsResponding(false);
    setMobileTab("pdf");
    router.push("/chat");
  }, [router]);

  const handleSelectChat = useCallback(
    async (chat: ChatApiResponse, options: SelectChatOptions = {}) => {
      if (loadingChatId === chat.id) return;

      abortRef.current?.abort();
      revokeBlobUrls(docsRef.current);
      selectionRef.current += 1;
      const selection = selectionRef.current;

      chatIdRef.current = chat.id;
      chatCreationRef.current = null;
      setActiveChatId(chat.id);
      setLoadingChatId(chat.id);
      setIsLoadingConversation(true);
      setIsResponding(false);
      setUploadError(null);
      setInput("");
      setDocs([]);
      setActiveId(null);
      setMessages([]);
      setMobileTab("pdf");

      if (!options.skipNavigation) {
        const href = `/chat/${encodeURIComponent(chat.id)}`;
        if (options.replace) router.replace(href);
        else router.push(href);
      }

      try {
        const response = await getChatDocuments(chat.id);
        if (selectionRef.current !== selection) return;

        const hydratedDocs = response.documents.map((document) =>
          pdfDocFromRecord(chat.id, document),
        );
        setDocs(hydratedDocs);
        setActiveId(hydratedDocs[0]?.id ?? null);
        setMessages(mapPersistedConversation(chat.conversation ?? []));
        upsertSavedChat({ ...chat, documents: response.documents });
      } catch (err) {
        if (selectionRef.current !== selection) return;
        setUploadError(
          err instanceof Error ? err.message : "Could not load this chat.",
        );
        setMessages(mapPersistedConversation(chat.conversation ?? []));
      } finally {
        if (selectionRef.current === selection) {
          setLoadingChatId(null);
          setIsLoadingConversation(false);
        }
      }
    },
    [loadingChatId, router, upsertSavedChat],
  );

  useEffect(() => {
    if (!initialChatId || chatsLoading || initialChatLoadedRef.current) return;

    const chat = savedChats.find((item) => item.id === initialChatId);
    const timeout = window.setTimeout(() => {
      initialChatLoadedRef.current = true;

      if (chat) {
        void handleSelectChat(chat, { replace: true, skipNavigation: true });
      } else {
        setUploadError("This chat link is no longer available.");
        router.replace("/chat");
      }
    }, 0);

    return () => window.clearTimeout(timeout);
  }, [chatsLoading, handleSelectChat, initialChatId, router, savedChats]);

  const uploadBatch = useCallback(
    async (newDocs: PdfDoc[], files: File[]) => {
      // Attachment ids that already existed before this batch.
      const prevIds = new Set(
        docsRef.current
          .map((d) => d.documentDbId)
          .filter((id): id is string => Boolean(id)),
      );

      try {
        const chatId = await ensureChat();
        const chat = await uploadPdfs(chatId, files);
        setActiveChatId(chat.id);
        upsertSavedChat(chat);

        const newPdfs = chat.documents.filter((p) => {
          const id = documentDbId(p);
          return id ? !prevIds.has(id) : true;
        });
        const remainingPdfs = [...newPdfs];

        for (const doc of newDocs) {
          const matchIdx = remainingPdfs.findIndex(
            (p) => p.filename === doc.name,
          );
          const match =
            matchIdx >= 0
              ? remainingPdfs.splice(matchIdx, 1)[0]
              : remainingPdfs.shift();

          if (match) {
            patchDoc(doc.id, {
              status: "ready",
              documentDbId: documentDbId(match),
              documentId: match.document_id,
              cloudinaryPages: match.pages ?? undefined,
            });
          } else {
            patchDoc(doc.id, {
              status: "error",
              error: "Upload did not complete",
            });
          }
        }
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Upload failed. Please try again.";
        setUploadError(message);
        for (const doc of newDocs) {
          patchDoc(doc.id, { status: "error", error: "Upload failed" });
        }
      }
    },
    [ensureChat, patchDoc, upsertSavedChat],
  );

  /** Turn validated files into local docs, then upload them in the background. */
  const addAcceptedFiles = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;

      // Dedupe against files already added (by name + size).
      const seen = new Set(docsRef.current.map((d) => `${d.name}:${d.sizeBytes}`));
      const unique: File[] = [];
      let hadDuplicate = false;
      for (const f of files) {
        const key = `${f.name}:${f.size}`;
        if (seen.has(key)) {
          hadDuplicate = true;
          continue;
        }
        seen.add(key);
        unique.push(f);
      }
      if (hadDuplicate) {
        setUploadError("Some files were already added and were skipped.");
      }
      if (unique.length === 0) return;

      const newDocs: PdfDoc[] = unique.map((file) => ({
        id: createId(),
        name: file.name,
        sizeBytes: file.size,
        url: URL.createObjectURL(file),
        status: "uploading",
        numPages: 0,
        lastPage: 1,
        scale: 1,
        fitWidth: true,
      }));

      setDocs((prev) => [...prev, ...newDocs]);
      setActiveId((prev) => prev ?? newDocs[0].id);
      setMobileTab("pdf");

      void uploadBatch(newDocs, unique);
    },
    [uploadBatch],
  );

  // Empty-state uploader already validated type/size; just add + upload.
  const handleEmptyStateFiles = useCallback(
    (files: File[]) => {
      setUploadError(null);
      addAcceptedFiles(files);
    },
    [addAcceptedFiles],
  );

  // Inline "Add PDF" from the tabs bar — validate here, then add.
  const handleTabsFiles = useCallback(
    (files: File[]) => {
      const { accepted, message } = validatePdfFiles(files, remaining, MAX_FILES);
      setUploadError(message);
      addAcceptedFiles(accepted);
    },
    [addAcceptedFiles, remaining],
  );

  const handleRemove = useCallback(
    async (id: string) => {
      const doc = docsRef.current.find((d) => d.id === id);
      if (!doc) return;

      // Confirm removal if the conversation already has messages.
      if (messages.length > 0) {
        const ok = window.confirm(
          `Remove "${doc.name}"? This won't delete your conversation, but the document will no longer be available.`,
        );
        if (!ok) return;
      }

      // Optimistically remove from the UI.
      setDocs((prev) => prev.filter((d) => d.id !== id));
      setActiveId((prev) => {
        if (prev !== id) return prev;
        const rest = docsRef.current.filter((d) => d.id !== id);
        return rest[0]?.id ?? null;
      });
      if (doc.url.startsWith("blob:")) URL.revokeObjectURL(doc.url);

      if (doc.documentDbId && chatIdRef.current) {
        try {
          const updatedChat = await deletePdf(chatIdRef.current, doc.documentDbId);
          upsertSavedChat(updatedChat);
        } catch (err) {
          setUploadError(
            err instanceof Error ? err.message : "Failed to delete from storage.",
          );
        }
      }
    },
    [messages.length, upsertSavedChat],
  );

  const goToPage = useCallback(
    (page: number) => {
      if (!activeDoc) return;
      const max = activeDoc.numPages || page;
      patchDoc(activeDoc.id, {
        lastPage: Math.min(Math.max(1, page), max),
      });
    },
    [activeDoc, patchDoc],
  );

  /**
   * Citation → viewer navigation: activate the cited document's tab (matched
   * by its content hash) and jump to the cited page.
   */
  const handleCitationClick = useCallback(
    (citation: Citation) => {
      const target = docsRef.current.find(
        (d) => d.documentId === citation.documentId,
      );
      if (target) {
        setActiveId(target.id);
        if (citation.pageNumber) {
          const max = target.numPages || citation.pageNumber;
          patchDoc(target.id, {
            lastPage: Math.min(Math.max(1, citation.pageNumber), max),
          });
        }
      } else if (citation.pageNumber) {
        goToPage(citation.pageNumber);
      }
      setMobileTab("pdf");
    },
    [goToPage, patchDoc],
  );

  /** Patch the assistant message currently being streamed (by id). */
  const patchMessage = useCallback(
    (id: string, updater: (prev: ChatMessage) => ChatMessage) => {
      setMessages((prev) =>
        prev.map((m) => (m.id === id ? updater(m) : m)),
      );
    },
    [],
  );

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const runSend = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || isResponding || isLoadingConversation) return;

      const readyDocs = docsRef.current.filter(
        (d) => d.status === "ready" && d.documentId,
      );
      if (readyDocs.length === 0 || !chatIdRef.current) return;

      // 1) User message appears immediately; 2) one empty assistant message
      // that every streamed token appends into.
      const assistantId = createId();
      setMessages((prev) => [
        ...prev,
        { id: createId(), role: "user", content: question, createdAt: Date.now() },
        {
          id: assistantId,
          role: "assistant",
          content: "",
          status: "streaming",
          statusText: "Understanding your question",
          createdAt: Date.now(),
        },
      ]);
      setInput("");
      setIsResponding(true);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await streamChat(
          chatIdRef.current,
          question,
          readyDocs.map((d) => d.documentId!),
          {
            onStatus: (message) =>
              patchMessage(assistantId, (m) => ({ ...m, statusText: message })),
            onIntent: (intent) => {
              if (intent.intent === "quiz") {
                console.log("Quiz intent detected", {
                  intent: intent.intent,
                  doc_ids: intent.doc_ids,
                  target: intent.target,
                  quiz_scope: intent.quiz_scope,
                  question_formats: intent.question_formats,
                  question_formats_mention_status:
                    intent.question_formats_mention_status,
                  difficulty: intent.difficulty,
                  number_of_questions: intent.number_of_questions,
                  number_of_questions_mention_status:
                    intent.number_of_questions_mention_status,
                  mode: intent.mode,
                  mode_mention_status: intent.mode_mention_status,
                  confidence: intent.confidence,
                });

                patchMessage(assistantId, (m) => ({
                  ...m,
                  statusText: "Preparing quiz",
                }));
                return;
              }

              if (intent.intent !== "summarization") return;

              const selectedNames = readyDocs
                .filter((doc) => intent.doc_ids.includes(doc.documentId ?? ""))
                .map((doc) => doc.name);
              const target = intent.target ? ` for "${intent.target}"` : "";
              const scope =
                selectedNames.length > 0
                  ? ` across ${selectedNames.join(", ")}`
                  : "";

              patchMessage(assistantId, (m) => ({
                ...m,
                statusText: `Preparing summary${target}${scope}`,
              }));
            },
            onToken: (token) =>
              patchMessage(assistantId, (m) => ({
                ...m,
                content: m.content + token,
              })),
            onCitations: (citations) =>
              patchMessage(assistantId, (m) => ({ ...m, citations })),
            onFinal: (structured, citations) =>
              patchMessage(assistantId, (m) => ({
                ...m,
                structured,
                citations: citations.length > 0 ? citations : m.citations,
              })),
            onError: (message) =>
              patchMessage(assistantId, (m) => ({
                ...m,
                status: "error",
                content: m.content || message,
              })),
          },
          controller.signal,
        );

        patchMessage(assistantId, (m) => ({
          ...m,
          status: m.status === "error" ? "error" : "complete",
        }));
      } catch (err) {
        const aborted =
          controller.signal.aborted ||
          (err instanceof DOMException && err.name === "AbortError");
        patchMessage(assistantId, (m) => {
          if (aborted) {
            // Keep whatever streamed in; drop the bubble if nothing arrived.
            return { ...m, status: "cancelled" };
          }
          return {
            ...m,
            status: "error",
            content:
              m.content ||
              "Sorry, something went wrong while answering. Please try again.",
          };
        });
        if (aborted) {
          setMessages((prev) =>
            prev.filter((m) => m.id !== assistantId || m.content.length > 0),
          );
        }
      } finally {
        abortRef.current = null;
        setIsResponding(false);
        void loadChats();
      }
    },
    [isLoadingConversation, isResponding, loadChats, patchMessage],
  );

  const hasDocs = docs.length > 0;
  const canChat = docs.some((d) => d.status === "ready");

  const renderPdfWorkspace = () => (
    <section className="flex h-full min-h-0 flex-col border-border md:border-r">
      {isLoadingConversation ? (
        <PdfLoadingSkeleton />
      ) : hasDocs ? (
        <>
          <PdfTabs
            docs={docs}
            activeId={activeId}
            maxFiles={MAX_FILES}
            onSelect={setActiveId}
            onRemove={handleRemove}
            onAddFiles={handleTabsFiles}
          />

          {remaining <= 0 ? (
            <p className="border-b border-border bg-card/30 px-3 py-1.5 text-center text-xs text-muted-foreground">
              You can upload a maximum of {MAX_FILES} PDFs in one chat.
            </p>
          ) : null}

          {uploadError ? (
            <div
              role="alert"
              className="flex items-start gap-2 border-b border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive"
            >
              <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
              <span>{uploadError}</span>
            </div>
          ) : null}

          {activeDoc ? (
            <>
              <PdfControls
                currentPage={activeDoc.lastPage}
                numPages={activeDoc.numPages}
                onPrev={() => goToPage(activeDoc.lastPage - 1)}
                onNext={() => goToPage(activeDoc.lastPage + 1)}
                onZoomIn={() =>
                  patchDoc(activeDoc.id, {
                    fitWidth: false,
                    scale: Math.min(MAX_SCALE, activeDoc.scale + SCALE_STEP),
                  })
                }
                onZoomOut={() =>
                  patchDoc(activeDoc.id, {
                    fitWidth: false,
                    scale: Math.max(MIN_SCALE, activeDoc.scale - SCALE_STEP),
                  })
                }
                onFitWidth={() => patchDoc(activeDoc.id, { fitWidth: true })}
                fitWidth={activeDoc.fitWidth}
              />
              <div className="min-h-0 flex-1">
                <PdfViewer
                  key={activeDoc.id}
                  fileUrl={activeDoc.url}
                  pageNumber={activeDoc.lastPage}
                  scale={activeDoc.scale}
                  fitWidth={activeDoc.fitWidth}
                  onLoadSuccess={(pages) =>
                    patchDoc(activeDoc.id, {
                      numPages: pages,
                      lastPage: Math.min(activeDoc.lastPage, pages),
                    })
                  }
                />
              </div>
            </>
          ) : (
            <div className="grid flex-1 place-items-center text-sm text-muted-foreground">
              Select a document tab to view it.
            </div>
          )}
        </>
      ) : (
        <div className="grid flex-1 place-items-center py-10">
          <PdfUploader
            onFilesSelected={handleEmptyStateFiles}
            remaining={remaining}
            maxFiles={MAX_FILES}
            externalError={uploadError}
          />
        </div>
      )}
    </section>
  );

  const renderChatPanel = () => (
    <section className="h-full min-h-0">
      <ChatPanel
        messages={messages}
        isResponding={isResponding}
        input={input}
        onInputChange={setInput}
        onSend={() => runSend(input)}
        onSelectSuggested={(q) => runSend(q)}
        onCitationClick={handleCitationClick}
        onStop={handleStop}
        hasDocument={canChat}
        isLoadingConversation={isLoadingConversation}
      />
    </section>
  );

  return (
    <div className="flex h-[100dvh] max-h-[100dvh] min-h-0 flex-col overflow-hidden">
      <ChatHeader documentCount={docs.length} maxFiles={MAX_FILES} />

      {/* Mobile panel switcher */}
      <div className="flex shrink-0 items-center gap-1 border-b border-border bg-card/40 p-1.5 md:hidden">
        {(
          [
            { id: "pdf", label: "Documents", icon: FileText },
            { id: "chat", label: "Chat", icon: MessagesSquare },
          ] as const
        ).map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setMobileTab(tab.id)}
            aria-pressed={mobileTab === tab.id}
            className={cn(
              "inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              mobileTab === tab.id
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <tab.icon className="size-4" />
            {tab.label}
          </button>
        ))}
      </div>

      <div className="hidden min-h-0 flex-1 overflow-hidden md:block">
        <ResizableSplit
          size={sidebarWidth}
          onSizeChange={setSidebarWidth}
          minSize={SIDEBAR_MIN_WIDTH}
          maxSize={SIDEBAR_MAX_WIDTH}
          minSecondSize={760}
          collapsedSize={SIDEBAR_COLLAPSED_WIDTH}
          collapseAt={SIDEBAR_COLLAPSE_AT}
          handleLabel="Resize chat history sidebar"
          first={
            <ChatHistorySidebar
              chats={savedChats}
              activeChatId={activeChatId}
              collapsed={sidebarCollapsed}
              loading={chatsLoading}
              loadingChatId={loadingChatId}
              error={chatsError}
              onSelectChat={handleSelectChat}
              onNewChat={handleNewChat}
              onToggleCollapsed={() =>
                setSidebarWidth((width) =>
                  width <= SIDEBAR_COLLAPSE_AT
                    ? SIDEBAR_DEFAULT_WIDTH
                    : SIDEBAR_COLLAPSED_WIDTH,
                )
              }
            />
          }
          second={
            <ResizableSplit
              size={pdfWidth}
              onSizeChange={setPdfWidth}
              minSize={PDF_MIN_WIDTH}
              maxSize={PDF_MAX_WIDTH}
              minSecondSize={CHAT_MIN_WIDTH}
              handleLabel="Resize PDF viewer"
              first={renderPdfWorkspace()}
              second={renderChatPanel()}
            />
          }
        />
      </div>

      <div className="min-h-0 flex-1 overflow-hidden md:hidden">
        <div
          className={cn(
            "h-full min-h-0",
            mobileTab === "pdf" ? "block" : "hidden",
          )}
        >
          {renderPdfWorkspace()}
        </div>
        <div
          className={cn(
            "h-full min-h-0",
            mobileTab === "chat" ? "block" : "hidden",
          )}
        >
          {renderChatPanel()}
        </div>
      </div>
    </div>
  );
}
