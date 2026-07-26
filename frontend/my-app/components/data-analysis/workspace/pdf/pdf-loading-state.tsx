import { FileText, Loader2 } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Shown while the PDFium engine boots or a document is being parsed.
 *
 * Mirrors the final frame (toolbar strip + page area) so opening a PDF does
 * not shift the layout, exactly like the Univer loading state does.
 */
export function PdfLoadingState({
  label = "Opening document…",
}: {
  label?: string;
}) {
  return (
    <div
      aria-busy
      aria-live="polite"
      className="flex h-full min-h-0 flex-col bg-background"
    >
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-2">
        <Skeleton className="size-7 rounded-md" />
        <Skeleton className="h-4 w-24" />
        <div className="flex-1" />
        <Skeleton className="h-4 w-16" />
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center p-6">
        <div className="flex flex-col items-center text-center">
          <span className="relative flex size-11 items-center justify-center rounded-xl border border-border bg-card text-[color:var(--accent-cyan)]">
            <FileText className="size-5" />
          </span>
          <p className="mt-3 flex items-center gap-1.5 text-sm text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            {label}
          </p>
        </div>
      </div>
    </div>
  );
}
