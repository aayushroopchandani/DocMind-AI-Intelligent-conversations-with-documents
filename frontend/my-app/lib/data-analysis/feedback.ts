import { toast } from "sonner";

/**
 * Honest "not wired up yet" feedback for controls that must be visible now
 * but only gain behaviour once the data-analysis backend ships.
 */
export const PENDING_FEATURE_MESSAGES = {
  import: "Spreadsheet import will be connected to the backend in a later milestone.",
  export: "Spreadsheet export will be connected to the backend in a later milestone.",
  share: "Sharing will be available once the backend integration lands.",
  dataSource: "Data sources will be connected to the backend in a later milestone.",
  analyst: "The data-analysis backend is still being connected.",
} as const;

export type PendingFeature = keyof typeof PENDING_FEATURE_MESSAGES;

export function notifyPendingFeature(feature: PendingFeature): void {
  toast.info(PENDING_FEATURE_MESSAGES[feature]);
}

export function notifyStorageFull(): void {
  toast.error(
    "Local draft could not be saved — this browser's storage is full.",
  );
}
