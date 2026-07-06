"use client";

import { useCallback, useEffect, useState } from "react";
import { FileText, MessagesSquare } from "lucide-react";
import type { ChatMessage, PdfDocumentInfo } from "@/lib/types";
import { sendMessage } from "@/lib/mock-chat";
import dynamic from "next/dynamic";
import { Loader2 } from "lucide-react";
import { ChatHeader } from "@/components/chat/chat-header";
import { PdfUploader } from "@/components/chat/pdf-uploader";
import { PdfControls } from "@/components/chat/pdf-controls";
import { ChatPanel } from "@/components/chat/chat-panel";
import { cn } from "@/lib/utils";

// react-pdf relies on browser-only APIs (DOMMatrix, canvas) at module evaluation
// time. Dynamic import with ssr:false keeps it out of the server bundle entirely.
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
 * Client-side orchestrator for the Chat with PDF page. Owns all interactive
 * state: the locally-selected PDF (browser memory only), viewer navigation,
 * and the conversation. No data is persisted or sent to a backend.
 */
export function ChatWorkspace() {
  const [document, setDocument] = useState<PdfDocumentInfo | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [scale, setScale] = useState(1);
  const [fitWidth, setFitWidth] = useState(true);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isResponding, setIsResponding] = useState(false);

  const [mobileTab, setMobileTab] = useState<MobileTab>("pdf");

  // Revoke the previous object URL whenever the document changes or unmounts.
  useEffect(() => {
    if (!document) return;
    const { url } = document;
    return () => URL.revokeObjectURL(url);
  }, [document]);

  const handleFileSelected = useCallback((file: File) => {
    const url = URL.createObjectURL(file);
    setDocument({ name: file.name, url, sizeBytes: file.size });
    setNumPages(0);
    setCurrentPage(1);
    setScale(1);
    setFitWidth(true);
    setMessages([]);
    setInput("");
    setMobileTab("pdf");
  }, []);

  const handleUploadAnother = useCallback(() => {
    // Clearing the document triggers the revoke effect above.
    setDocument(null);
    setMessages([]);
    setInput("");
  }, []);

  const goToPage = useCallback(
    (page: number) => {
      setCurrentPage((prev) => {
        const max = numPages || prev;
        return Math.min(Math.max(1, page), max);
      });
    },
    [numPages],
  );

  const handleCitationClick = useCallback(
    (page: number) => {
      goToPage(page);
      setMobileTab("pdf"); // surface the source on mobile
    },
    [goToPage],
  );

  const runSend = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || !document || isResponding) return;

      const userMessage: ChatMessage = {
        id: createId(),
        role: "user",
        content: question,
        createdAt: Date.now(),
      };
      setMessages((prev) => [...prev, userMessage]);
      setInput("");
      setIsResponding(true);

      try {
        const response = await sendMessage(question, document.name);
        setMessages((prev) => [
          ...prev,
          {
            id: createId(),
            role: "assistant",
            content: response.content,
            citations: response.citations,
            createdAt: Date.now(),
          },
        ]);
      } catch {
        setMessages((prev) => [
          ...prev,
          {
            id: createId(),
            role: "assistant",
            content: "Sorry, something went wrong while answering. Please try again.",
            createdAt: Date.now(),
          },
        ]);
      } finally {
        setIsResponding(false);
      }
    },
    [document, isResponding],
  );

  const hasDocument = document !== null;

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <ChatHeader document={document} onUploadAnother={handleUploadAnother} />

      {/* Mobile panel switcher */}
      <div className="flex items-center gap-1 border-b border-border bg-card/40 p-1.5 md:hidden">
        {(
          [
            { id: "pdf", label: "Document", icon: FileText },
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
        {/* PDF side */}
        <section
          className={cn(
            "flex min-h-0 flex-col border-border md:border-r",
            mobileTab === "pdf" ? "flex" : "hidden md:flex",
          )}
        >
          {hasDocument ? (
            <>
              <PdfControls
                currentPage={currentPage}
                numPages={numPages}
                onPrev={() => goToPage(currentPage - 1)}
                onNext={() => goToPage(currentPage + 1)}
                onZoomIn={() => {
                  setFitWidth(false);
                  setScale((s) => Math.min(MAX_SCALE, s + SCALE_STEP));
                }}
                onZoomOut={() => {
                  setFitWidth(false);
                  setScale((s) => Math.max(MIN_SCALE, s - SCALE_STEP));
                }}
                onFitWidth={() => setFitWidth(true)}
                fitWidth={fitWidth}
              />
              <div className="min-h-0 flex-1">
                <PdfViewer
                  fileUrl={document.url}
                  pageNumber={currentPage}
                  scale={scale}
                  fitWidth={fitWidth}
                  onLoadSuccess={(pages) => {
                    setNumPages(pages);
                    setCurrentPage((p) => Math.min(p, pages));
                  }}
                />
              </div>
            </>
          ) : (
            <div className="grid flex-1 place-items-center py-10">
              <PdfUploader onFileSelected={handleFileSelected} />
            </div>
          )}
        </section>

        {/* Chat side */}
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
            hasDocument={hasDocument}
          />
        </section>
      </div>
    </div>
  );
}
