import { PDF_CITATION_PULSE_MS } from "@/lib/data-analysis/constants";
import type { PdfCitation } from "@/lib/data-analysis/pdf/pdf-types";

/**
 * Tiny observable store for AI citation highlights.
 *
 * Nothing writes to it today — the analyst backend is not connected, and no
 * placeholder citations are ever injected. It exists so the highlight layer
 * is real and already wired: when the agent ships, `highlightCitation()` on
 * the controller is the only entry point that needs calling.
 *
 * Kept module-level (not React state) so the controller can drive highlights
 * from outside the tree, exactly like `univer-bridge` does for Univer.
 */

export interface ActiveCitation extends PdfCitation {
  /** Distinguishes repeat highlights of the same region so pulses restart. */
  token: number;
  /** True while the pulse animation should run. */
  pulsing: boolean;
}

type Listener = () => void;

const listeners = new Set<Listener>();
let active: ActiveCitation | null = null;
let token = 0;
let pulseTimer: ReturnType<typeof setTimeout> | null = null;

function emit(): void {
  for (const listener of listeners) listener();
}

function clearPulseTimer(): void {
  if (pulseTimer) {
    clearTimeout(pulseTimer);
    pulseTimer = null;
  }
}

export function subscribeToCitation(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Snapshot for `useSyncExternalStore` — stable identity between changes. */
export function getCitationSnapshot(): ActiveCitation | null {
  return active;
}

/** Server snapshot: highlights only ever exist after a client interaction. */
export function getCitationServerSnapshot(): ActiveCitation | null {
  return null;
}

export function setCitation(citation: PdfCitation): ActiveCitation {
  clearPulseTimer();
  token += 1;
  active = { ...citation, token, pulsing: true };
  emit();

  // The highlight persists; only the attention-grabbing pulse expires.
  pulseTimer = setTimeout(() => {
    pulseTimer = null;
    if (!active) return;
    active = { ...active, pulsing: false };
    emit();
  }, PDF_CITATION_PULSE_MS);

  return active;
}

/** Clears the highlight, optionally only if it belongs to one artifact. */
export function clearCitation(artifactId?: string): void {
  if (!active) return;
  if (artifactId && active.artifactId !== artifactId) return;
  clearPulseTimer();
  active = null;
  emit();
}
