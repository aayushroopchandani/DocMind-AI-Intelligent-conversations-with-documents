"use client";

import { useEffect, useRef } from "react";
import { useCoreState } from "@embedpdf/core/react";
import { PdfErrorCode } from "@embedpdf/models";
import { useDocumentManagerCapability } from "@embedpdf/plugin-document-manager/react";
import { loadPdfBuffer } from "@/lib/data-analysis/pdf/pdf-storage";
import { isPdfArtifact } from "@/lib/data-analysis/types";
import { openArtifactsOfType } from "@/lib/data-analysis/workspace-state";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * Keeps EmbedPDF's open documents in lockstep with the workspace's open PDF
 * tabs. Renders nothing.
 *
 * This is the PDF counterpart to the unit-reconciliation effect in
 * `univer-host`: one engine holds many documents (one per tab), opening a tab
 * loads its blob from IndexedDB, closing a tab closes just that document, and
 * activating a tab calls `setActiveDocument`. Because the engine retains each
 * document, page/zoom/rotation/scroll/search state survives tab switches with
 * no snapshotting on our side.
 */
export function PdfDocumentSync() {
  const { state, actions } = useWorkspace();
  const { provides: documents } = useDocumentManagerCapability();
  const coreState = useCoreState();

  // Documents we have asked the engine to open, so a re-run never double-opens.
  const requestedRef = useRef(new Set<string>());
  // Guards async work against unmount / Strict Mode's double effect run.
  const aliveRef = useRef(true);

  const openPdfIds = openArtifactsOfType(state, "pdf").map(
    (artifact) => artifact.id,
  );
  const openPdfKey = openPdfIds.join("|");

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  /* ---------------- open / close documents ---------------- */

  useEffect(() => {
    if (!documents) return;
    // Bound locally so the async helper below needs no non-null assertion.
    const manager = documents;
    const requested = requestedRef.current;
    const wanted = new Set(openPdfIds);

    // Close documents whose tab is gone (closed or deleted).
    for (const documentId of [...requested]) {
      if (wanted.has(documentId)) continue;
      requested.delete(documentId);
      if (manager.isDocumentOpen(documentId)) {
        // Releases the parsed document and its render caches in the engine.
        manager
          .closeDocument(documentId)
          .toPromise()
          .catch(() => {
            // Already gone — nothing to release.
          });
      }
    }

    // Open documents for newly opened tabs.
    for (const documentId of wanted) {
      if (requested.has(documentId)) continue;
      requested.add(documentId);
      void openDocument(documentId);
    }

    async function openDocument(documentId: string) {
      const artifact = state.artifacts.find((item) => item.id === documentId);
      if (!isPdfArtifact(artifact)) return;

      const buffer = await loadPdfBuffer(artifact.pdf.storageKey);
      // Bail if the tab closed (or the host unmounted) while we were reading.
      if (!aliveRef.current || !requestedRef.current.has(documentId)) return;

      if (!buffer) {
        actions.patchPdfMeta(documentId, {
          loadingStatus: "missing",
          errorMessage: undefined,
        });
        return;
      }

      try {
        // The engine takes ownership of the buffer from here — we keep no
        // reference to it, so the bytes exist exactly once in memory.
        const { task } = await manager
          .openDocumentBuffer({
            documentId,
            name: artifact.pdf.originalFileName,
            buffer,
            // The workspace decides which tab is active; opening a background
            // tab's document must not steal focus from the active one.
            autoActivate: false,
          })
          .toPromise();
        await task.toPromise();
      } catch (error) {
        if (!aliveRef.current) return;
        actions.patchPdfMeta(documentId, {
          loadingStatus: "error",
          errorMessage: describeOpenFailure(error),
        });
      }
    }
    // `state.artifacts` is read inside but intentionally not a dependency:
    // metadata patches would otherwise re-run this reconciliation loop. Tab
    // membership (openPdfKey) is the only thing that should trigger it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documents, openPdfKey, actions]);

  /* ---------------- follow the active tab ---------------- */

  useEffect(() => {
    if (!documents) return;
    const activeId = state.activeTabId;
    if (!activeId || !openPdfIds.includes(activeId)) return;
    if (!documents.isDocumentOpen(activeId)) return;
    if (documents.getActiveDocumentId() === activeId) return;
    documents.setActiveDocument(activeId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documents, state.activeTabId, openPdfKey, coreState?.documents]);

  /* ---------------- mirror engine status into metadata ---------------- */

  useEffect(() => {
    if (!coreState) return;
    for (const documentId of openPdfIds) {
      const documentState = coreState.documents[documentId];
      if (!documentState) continue;

      if (documentState.status === "loaded") {
        actions.patchPdfMeta(documentId, {
          loadingStatus: "ready",
          pageCount: documentState.document?.pageCount,
          errorMessage: undefined,
        });
      } else if (documentState.status === "error") {
        actions.patchPdfMeta(documentId, {
          loadingStatus: "error",
          errorMessage: describeErrorCode(documentState.errorCode),
        });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coreState?.documents, openPdfKey, actions]);

  return null;
}

const PASSWORD_MESSAGE =
  "This PDF is password-protected. Encrypted documents are not supported yet.";
const DAMAGED_MESSAGE =
  "This PDF could not be opened — the file is damaged or is not a valid PDF.";

/**
 * PDFium reports failures as internal strings such as
 * "FPDF_LoadMemDocument failed", which mean nothing to a user. The numeric
 * error code is the stable signal, so translate that instead and never surface
 * the raw engine text.
 */
function describeErrorCode(code: PdfErrorCode | undefined): string {
  switch (code) {
    case PdfErrorCode.Password:
      return PASSWORD_MESSAGE;
    case PdfErrorCode.Security:
      return "This PDF uses a security scheme this viewer cannot open.";
    case PdfErrorCode.NotFound:
      return "The PDF data could not be read back from this browser's storage.";
    default:
      return DAMAGED_MESSAGE;
  }
}

function describeOpenFailure(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  if (/password/i.test(message)) return PASSWORD_MESSAGE;
  return DAMAGED_MESSAGE;
}
