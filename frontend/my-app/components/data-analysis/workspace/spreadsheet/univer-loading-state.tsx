import { Loader2 } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Placeholder shown while the Univer bundle loads. Mimics a spreadsheet
 * frame (toolbar / formula bar / grid) so there is no layout shift when the
 * real editor appears.
 */
export function UniverLoadingState() {
  return (
    <div
      role="status"
      aria-label="Loading spreadsheet editor"
      className="flex h-full w-full flex-col gap-2 p-3"
    >
      <Skeleton className="h-9 w-full rounded-lg" />
      <Skeleton className="h-7 w-2/3 rounded-lg" />
      <div className="relative flex-1 overflow-hidden rounded-lg border border-border bg-card/40">
        <div className="absolute inset-0 grid grid-cols-8 grid-rows-12 opacity-40">
          {Array.from({ length: 96 }).map((_, index) => (
            <div key={index} className="border-[0.5px] border-border/50" />
          ))}
        </div>
        <div className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Loading spreadsheet editor…
        </div>
      </div>
    </div>
  );
}
