"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { FileText, MessagesSquare, Loader2, AlertCircle } from "lucide-react";
import type { ChatMessage, Citation, PdfDoc } from "@/lib/types";
import { createChat, uploadPdfs, deletePdf, streamChat } from "@/lib/api";
import { validatePdfFiles } from "@/lib/pdf";
import { ChatHeader } from "@/components/chat/chat-header";
import { PdfUploader } from "@/components/chat/pdf-uploader";
import { PdfTabs } from "@/components/chat/pdf-tabs";
import { PdfControls } from "@/components/chat/pdf-controls";
import { ChatPanel } from "@/components/chat/chat-panel";
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

type MobileTab = "pdf" | "chat";

function createId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * Client-side orchestrator for the multi-PDF Chat workspace.
 *
 * Owns: the set of uploaded PDFs (each with its own viewer state), the active
 * tab, the backing chat id, and the conversation. PDFs render instantly from a
 * local blob URL and are uploaded to Cloudinary (persisted per-chat in MongoDB)
 * in the background.
 */
export function ChatWorkspace() {
  const [docs, setDocs] = useState<PdfDoc[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isResponding, setIsResponding] = useState(false);
  const [mobileTab, setMobileTab] = useState<MobileTab>("pdf");

  // Lazily-created chat id + a guard so concurrent uploads share one creation.
  const chatIdRef = useRef<string | null>(null);
  const chatCreationRef = useRef<Promise<string> | null>(null);
  // Abort controller for the in-flight answer stream (stop button / unmount).
  const abortRef = useRef<AbortController | null>(null);
  // Mirror of docs for use inside async callbacks without stale closures.
  const docsRef = useRef<PdfDoc[]>([]);
  useEffect(() => {
    docsRef.current = docs;
  }, [docs]);

  // Revoke every object URL + abort any in-flight stream on unmount.
  useEffect(() => {
    return () => {
      for (const d of docsRef.current) URL.revokeObjectURL(d.url);
      abortRef.current?.abort();
    };
  }, []);

  const activeDoc = docs.find((d) => d.id === activeId) ?? null;
  const remaining = MAX_FILES - docs.length;

  const patchDoc = useCallback((id: string, patch: Partial<PdfDoc>) => {
    setDocs((prev) => prev.map((d) => (d.id === id ? { ...d, ...patch } : d)));
  }, []);

  const ensureChat = useCallback(async (): Promise<string> => {
    if (chatIdRef.current) return chatIdRef.current;
    if (chatCreationRef.current) return chatCreationRef.current;

    const promise = createChat()
      .then((chat) => {
        chatIdRef.current = chat.id;
        return chat.id;
      })
      .finally(() => {
        chatCreationRef.current = null;
      });
    chatCreationRef.current = promise;
    return promise;
  }, []);

  const uploadBatch = useCallback(
    async (newDocs: PdfDoc[], files: File[]) => {
      // public_ids that already existed before this batch.
      const prevIds = new Set(
        docsRef.current.filter((d) => d.publicId).map((d) => d.publicId),
      );

      try {
        const chatId = await ensureChat();
        const chat = await uploadPdfs(chatId, files);

        const newPdfs = chat.pdf.filter((p) => !prevIds.has(p.public_id));
        const remainingPdfs = [...newPdfs];

        for (const doc of newDocs) {
          const matchIdx = remainingPdfs.findIndex(
            (p) => p.filename === doc.name,
          );
          const match =
            matchIdx >= 0 ? remainingPdfs.splice(matchIdx, 1)[0] : undefined;

          if (match) {
            patchDoc(doc.id, {
              status: "ready",
              publicId: match.public_id,
              secureUrl: match.secure_url,
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
    [ensureChat, patchDoc],
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
      URL.revokeObjectURL(doc.url);

      if (doc.publicId && chatIdRef.current) {
        try {
          await deletePdf(chatIdRef.current, doc.publicId);
        } catch (err) {
          setUploadError(
            err instanceof Error ? err.message : "Failed to delete from storage.",
          );
        }
      }
    },
    [messages.length],
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
   * by Cloudinary public_id) and jump to the cited page.
   */
  const handleCitationClick = useCallback(
    (citation: Citation) => {
      const target = docsRef.current.find(
        (d) => d.publicId === citation.documentId,
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
      if (!question || isResponding) return;

      const readyDocs = docsRef.current.filter(
        (d) => d.status === "ready" && d.publicId,
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
          readyDocs.map((d) => d.publicId!),
          {
            onStatus: (message) =>
              patchMessage(assistantId, (m) => ({ ...m, statusText: message })),
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
      }
    },
    [isResponding, patchMessage],
  );

  const hasDocs = docs.length > 0;
  const canChat = docs.some((d) => d.status === "ready");

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <ChatHeader documentCount={docs.length} maxFiles={MAX_FILES} />

      {/* Mobile panel switcher */}
      <div className="flex items-center gap-1 border-b border-border bg-card/40 p-1.5 md:hidden">
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

      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-2">
        {/* PDF workspace */}
        <section
          className={cn(
            "flex min-h-0 flex-col border-border md:border-r",
            mobileTab === "pdf" ? "flex" : "hidden md:flex",
          )}
        >
          {hasDocs ? (
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

        {/* Chat panel */}
        <section
          className={cn(
            "min-h-0",
            mobileTab === "chat" ? "block" : "hidden md:block",
          )}
        >
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
          />
        </section>
      </div>
    </div>
  );
}
