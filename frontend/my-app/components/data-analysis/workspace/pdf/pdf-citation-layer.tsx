"use client";

import { useSyncExternalStore } from "react";
import { useDocumentState } from "@embedpdf/core/react";
import {
  getCitationServerSnapshot,
  getCitationSnapshot,
  subscribeToCitation,
} from "@/lib/data-analysis/pdf/pdf-citations";

interface PdfCitationLayerProps {
  documentId: string;
  pageIndex: number;
}

/**
 * Renders the highlight for an AI-cited region on one page.
 *
 * Nothing produces citations yet — the analyst backend is not connected — so
 * in practice this layer renders `null`. It is real rather than stubbed so
 * that `highlightCitation()` on the PDF controller works the day the agent
 * starts returning sources, with no viewer changes needed.
 *
 * Rects arrive in unscaled page coordinates and are multiplied by the
 * document's live scale, matching how EmbedPDF's own search layer works.
 */
export function PdfCitationLayer({
  documentId,
  pageIndex,
}: PdfCitationLayerProps) {
  const citation = useSyncExternalStore(
    subscribeToCitation,
    getCitationSnapshot,
    getCitationServerSnapshot,
  );
  const documentState = useDocumentState(documentId);
  const scale = documentState?.scale ?? 1;

  if (!citation || citation.artifactId !== documentId) return null;
  if (citation.pageNumber - 1 !== pageIndex) return null;

  const boxes = citation.boundingBoxes ?? [];
  if (boxes.length === 0) return null;

  return (
    <div
      aria-hidden
      style={{ position: "absolute", inset: 0, pointerEvents: "none" }}
    >
      {boxes.map((box, index) => (
        <div
          key={`${citation.token}-${index}`}
          style={{
            position: "absolute",
            top: box.y * scale,
            left: box.x * scale,
            width: box.width * scale,
            height: box.height * scale,
            borderRadius: 2,
            backgroundColor:
              "color-mix(in oklch, var(--accent-cyan) 28%, transparent)",
            outline:
              "1px solid color-mix(in oklch, var(--accent-cyan) 70%, transparent)",
            mixBlendMode: "screen",
          }}
          className={citation.pulsing ? "animate-pulse" : undefined}
        />
      ))}
    </div>
  );
}
